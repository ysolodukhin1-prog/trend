from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid


TERMINAL_LINE_LIMIT = 1200
TERMINAL_TASK_STATUSES = {"ok", "limited", "skipped"}
DEFAULT_FAILURE_POLICY = os.environ.get("ADMIN_ALL_CLIENTS_FAILURE_POLICY", "skip")
DEFAULT_RETRY_BACKOFF_SECONDS = max(1, int(os.environ.get("ADMIN_ALL_CLIENTS_RETRY_BACKOFF_SECONDS", "60")))
CONSECUTIVE_FAILURE_LIMIT = max(1, int(os.environ.get("ADMIN_ALL_CLIENTS_FAILURE_LIMIT", "5")))
MAX_WORKERS = max(1, int(os.environ.get("ADMIN_ALL_CLIENTS_MAX_WORKERS", "12")))
MAX_VIEW_WORKERS = max(1, int(os.environ.get("ADMIN_ALL_CLIENTS_MAX_VIEW_WORKERS", "2")))
# Клиенты с самыми объёмными базами: две такие перестройки витрин одновременно
# упираются в диск, а не в лимиты API, поэтому ограничитель действует только на views.
HEAVY_CLIENTS = {
    value.strip()
    for value in os.environ.get("ADMIN_ALL_CLIENTS_HEAVY_CLIENTS", "gloria_jeans,sportmaster").split(",")
    if value.strip()
}
HEAVY_CLIENT_LIMIT = max(1, int(os.environ.get("ADMIN_ALL_CLIENTS_HEAVY_LIMIT", "1")))
CATCH_UP_PASS_LIMIT = max(0, int(os.environ.get("ADMIN_ALL_CLIENTS_CATCH_UP_PASSES", "3")))
OZON_PERFORMANCE_ACCESS_PATTERNS = (
    "у организации нет доступа к ozon performance api",
    "ozon api http 403 for get https://api-performance.ozon.ru/api/client/campaign",
)
PROGRESS_RE = re.compile(r"(?:ПРОГРЕСС|PROGRESS).*?(\d+)\s*/\s*(\d+).*?\((\d+(?:[.,]\d+)?)%\)", re.I)
ROWS_RE = re.compile(r"(?:строк(?:и|а)?|rows?)\s*[:=]?\s*([\d\s]+)(?:\s*/\s*([\d\s?]+))?", re.I)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _elapsed_seconds(started_at):
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return 0
    return max(0, int((datetime.now() - started).total_seconds()))


def _reclassify_persisted_access_limit(state):
    if state.get("status") != "error":
        return False
    error_task = next(
        (
            task for task in state.get("tasks") or []
            if task.get("status") == "error" and "ozon_advertising" in str(task.get("key") or "")
        ),
        None,
    )
    if not error_task:
        return False
    output = "\n".join(str(row.get("text") or "") for row in (state.get("logs") or [])[-80:]).casefold()
    if not any(pattern in output for pattern in OZON_PERFORMANCE_ACCESS_PATTERNS):
        return False
    error_task.update({
        "status": "limited",
        "detail": "Нет доступа",
        "progress_pct": 100,
        "progress_text": "Нет доступа",
    })
    state["status"] = "stopped"
    state["message"] = "Ozon Performance недоступен для аккаунта; можно продолжить остальные этапы"
    state["current"] = None
    state.setdefault("logs", []).append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": "warning",
        "text": "Сохранённая ошибка доступа Ozon Performance переведена в ограничение источника",
    })
    state["updated_at"] = _now()
    return True


class AllClientsDailyRunner:
    def __init__(
        self,
        state_path,
        plan_provider,
        acquire_task=None,
        register_process=None,
        release_task=None,
        terminate_process=None,
        process_group_kwargs=None,
    ):
        self.state_path = Path(state_path)
        self.plan_provider = plan_provider
        self.acquire_task = acquire_task
        self.register_process = register_process
        self.release_task = release_task
        self.terminate_process = terminate_process
        self.process_group_kwargs = process_group_kwargs or (lambda: {})
        self.lock = threading.RLock()
        self.thread = None
        self.process = None
        self.processes = {}
        self.max_workers = MAX_WORKERS
        self.max_view_workers = MAX_VIEW_WORKERS
        self.stop_requested = False
        self.paused_clients = set()
        self.replay_clients = set()
        # The next execution of these tasks must use the collector checkpoint.
        self.resume_task_ids = set()
        self._consecutive_failures = 0
        self._last_save = 0.0
        self._last_idle_plan_refresh = 0.0
        self.state = self._load_state()

    def _load_state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            state = self._empty_state()
        if state.get("status") in {"running", "stopping"}:
            state["status"] = "stopped"
            state["message"] = "Сервер был перезапущен. Продолжите процесс с незавершённого этапа."
            current_id = str((state.get("current") or {}).get("task_id") or "")
            for task in state.get("tasks") or []:
                if task.get("id") == current_id and task.get("status") == "running":
                    task["status"] = "stopped"
                    task["detail"] = "Остановлено перезапуском сервера"
            state.setdefault("logs", []).append({"time": _now()[11:19], "type": "stopped", "text": state["message"]})
            state["updated_at"] = _now()
            self._write_state(state)
        elif _reclassify_persisted_access_limit(state):
            self._write_state(state)
        return state

    @staticmethod
    def _empty_state():
        return {
            "ok": True,
            "run_id": "",
            "status": "idle",
            "message": "Общий процесс ещё не запускался",
            "started_at": "",
            "updated_at": "",
            "finished_at": "",
            "current": None,
            "clients": [],
            "rows": [],
            "tasks": [],
            "logs": [],
        }

    def _write_state(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_suffix(self.state_path.suffix + f".{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(5):
                try:
                    os.replace(temp_path, self.state_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _save_locked(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_save < 1.0:
            return
        self.state["updated_at"] = _now()
        self._write_state(self.state)
        self._last_save = now

    def _append_log_locked(self, text, line_type="output"):
        value = str(text or "").rstrip()
        if not value:
            return
        self.state.setdefault("logs", []).append({"time": datetime.now().strftime("%H:%M:%S"), "type": line_type, "text": value})
        if len(self.state["logs"]) > TERMINAL_LINE_LIMIT:
            self.state["logs"] = self.state["logs"][-TERMINAL_LINE_LIMIT:]

    @staticmethod
    def _public_plan(plan):
        public_keys = (
            "id", "row_id", "row_label", "row_order", "stage",
            "source_kind", "source_label", "client", "client_label", "key", "report",
            "coverage_from", "coverage_to", "expected_from", "expected_to",
            "missing_windows", "missing_days", "coverage_label", "missing_label",
            "cell_label", "snapshot", "retention_days", "inspection_error",
        )
        clients = [{"key": row["key"], "label": row["label"]} for row in plan.get("clients") or []]
        tasks = []
        rows = []
        seen_rows = set()
        for raw in plan.get("tasks") or []:
            task = {key: raw.get(key, "") for key in public_keys}
            initial_status = str(raw.get("initial_status") or "queued")
            task.update({
                "status": initial_status,
                "detail": raw.get("initial_detail") or ("Данные актуальны" if initial_status == "ok" else "В очереди"),
                "progress_pct": 100 if initial_status == "ok" else 0,
                "progress_text": raw.get("initial_progress_text") or ("Актуально" if initial_status == "ok" else "В очереди"),
            })
            tasks.append(task)
            if task["row_id"] not in seen_rows:
                seen_rows.add(task["row_id"])
                rows.append({
                    "id": task["row_id"],
                    "label": task["row_label"],
                    "stage": task["stage"],
                    "order": task.get("row_order") or 900,
                })
        rows.sort(key=lambda row: (row.get("order", 900), row.get("label", "")))
        return clients, rows, tasks

    @staticmethod
    def _tasks_in_run_scope(state, tasks):
        task_scope = set(state.get("scope_task_ids") or [])
        client_scope = set(state.get("scope_clients") or [])
        if task_scope:
            return [task for task in tasks if task.get("id") in task_scope]
        if client_scope:
            return [task for task in tasks if task.get("client") in client_scope]
        return tasks

    def snapshot(self):
        with self.lock:
            refresh_available_plan = (
                self.state.get("status") not in {"running", "stopping"}
                and (
                    not self.state.get("tasks")
                    or time.monotonic() - self._last_idle_plan_refresh >= 30
                )
            )
            if refresh_available_plan:
                plan = self.plan_provider()
                clients, rows, tasks = self._public_plan(plan)
                if tasks:
                    previous = {task.get("id"): task for task in self.state.get("tasks") or []}
                    for task in tasks:
                        old = previous.get(task.get("id"))
                        if old and old.get("status") not in {"running", "stopping"}:
                            task.update({
                                "status": old.get("status") or task.get("status"),
                                "detail": old.get("detail") or task.get("detail"),
                                "progress_pct": old.get("progress_pct") if old.get("progress_pct") is not None else task.get("progress_pct"),
                                "progress_text": old.get("progress_text") or task.get("progress_text"),
                            })
                            for runtime_key in ("attempts", "rows_text", "started_at", "finished_at"):
                                if runtime_key in old:
                                    task[runtime_key] = old[runtime_key]
                        elif self.state.get("status") == "idle" and task.get("status") != "ok":
                            task.update({"status": "idle", "detail": task.get("detail") or "Ещё не запускался"})
                    self.state.update({"clients": clients, "rows": rows, "tasks": tasks})
                    if self.state.get("status") == "completed" and any(
                        task.get("status") not in TERMINAL_TASK_STATUSES
                        for task in self._tasks_in_run_scope(self.state, tasks)
                    ):
                        self.state["status"] = "idle"
                        self.state["message"] = "Есть этапы для обновления"
                self._last_idle_plan_refresh = time.monotonic()
                self._save_locked(force=True)
            state = deepcopy(self.state)
            if state.get("status") in {"running", "stopping"}:
                state["elapsed_seconds"] = _elapsed_seconds(state.get("started_at"))
            else:
                state["elapsed_seconds"] = int(state.get("elapsed_seconds") or _elapsed_seconds(state.get("started_at")))
            tasks = state.get("tasks") or []
            state["completed"] = sum(1 for task in tasks if task.get("status") in TERMINAL_TASK_STATUSES)
            state["limited"] = sum(1 for task in tasks if task.get("status") == "limited")
            state["failed"] = sum(1 for task in tasks if task.get("status") == "error")
            state["active"] = list(state.get("active") or [])
            state["max_workers"] = self.max_workers
            state["max_view_workers"] = self.max_view_workers
            state["paused_clients"] = sorted(self.paused_clients)
            state["scope_clients"] = list(state.get("scope_clients") or [])
            state["scope_task_ids"] = list(state.get("scope_task_ids") or [])
            running = state.get("status") in {"running", "stopping"}
            active_clients = {row.get("client") for row in state["active"] if row}
            client_controls = {}
            for client in state.get("clients") or []:
                key = client.get("key")
                own = [task for task in tasks if task.get("client") == key]
                unfinished = [
                    task for task in own if task.get("status") not in TERMINAL_TASK_STATUSES
                ]
                client_controls[key] = {
                    "total": len(own),
                    "unfinished": len(unfinished),
                    "paused": key in self.paused_clients,
                    "active": key in active_clients,
                    "can_start": bool(own) and not running,
                    "can_resume": bool(unfinished)
                    and (not running or key in self.paused_clients),
                    "can_stop": running
                    and key not in self.paused_clients
                    and bool(unfinished),
                }
            state["client_controls"] = client_controls
            state["total"] = len(tasks)
            progress_units = sum(
                1 if task.get("status") in TERMINAL_TASK_STATUSES else max(0, min(100, float(task.get("progress_pct") or 0))) / 100
                for task in tasks
            )
            state["progress_pct"] = round((progress_units / len(tasks)) * 100, 1) if tasks else 0
            state["can_start"] = state.get("status") not in {"running", "stopping"}
            state["can_stop"] = state.get("status") in {"running", "stopping"}
            state["can_resume"] = state.get("status") in {"error", "stopped"} and any(
                task.get("status") not in TERMINAL_TASK_STATUSES for task in tasks
            )
            return state

    def start(self, resume=False, only_clients=None, only_task_ids=None):
        plan = self.plan_provider()
        clients, rows, fresh_tasks = self._public_plan(plan)
        if not fresh_tasks:
            raise ValueError("Нет подключённых ежедневных процессов")
        scope = {str(value) for value in (only_clients or []) if str(value or "").strip()}
        task_scope = {str(value) for value in (only_task_ids or []) if str(value or "").strip()}
        if only_task_ids is not None and not task_scope:
            raise ValueError("Выберите хотя бы один этап")
        if task_scope:
            known_task_ids = {str(task.get("id") or "") for task in fresh_tasks}
            unknown_task_ids = task_scope - known_task_ids
            if unknown_task_ids:
                raise ValueError(f"Неизвестные этапы: {', '.join(sorted(unknown_task_ids))}")
            if scope:
                foreign = {
                    task["id"] for task in fresh_tasks
                    if task["id"] in task_scope and task.get("client") not in scope
                }
                if foreign:
                    raise ValueError("Выбранный этап относится к другому аккаунту")
        if scope:
            known = {task.get("client") for task in fresh_tasks}
            unknown = scope - known
            if unknown:
                raise ValueError(f"Нет этапов для аккаунта: {', '.join(sorted(unknown))}")
            if not task_scope and not any(
                task.get("client") in scope and task.get("status") not in TERMINAL_TASK_STATUSES
                for task in fresh_tasks
            ):
                raise ValueError("У аккаунта нет незавершённых этапов")
        with self.lock:
            if self.state.get("status") in {"running", "stopping"}:
                raise RuntimeError("Обновление всех аккаунтов уже выполняется")
            if resume or scope or task_scope:
                # При запуске одного аккаунта состояние остальных сохраняем целиком,
                # иначе кнопка по клиенту обнуляла бы всю матрицу.
                previous = {task.get("id"): task for task in self.state.get("tasks") or []}
                for task in fresh_tasks:
                    old = previous.get(task["id"])
                    if ((scope and task.get("client") not in scope) or (task_scope and task["id"] not in task_scope)) and old:
                        task.update({
                            "status": old.get("status") or task["status"],
                            "detail": old.get("detail") or task["detail"],
                            "progress_pct": old.get("progress_pct") or task["progress_pct"],
                            "progress_text": old.get("progress_text") or task["progress_text"],
                        })
                        continue
                    if task_scope and task["id"] not in task_scope:
                        task.update({
                            "status": "skipped",
                            "detail": "Не выбрано",
                            "progress_pct": 0,
                            "progress_text": "Не выбрано",
                        })
                        continue
                    if task_scope and task["id"] in task_scope:
                        task.update({
                            "status": "queued",
                            "detail": "Выбрано для запуска",
                            "progress_pct": 0,
                            "progress_text": "Ждёт",
                        })
                        continue
                    if resume and old and old.get("status") in TERMINAL_TASK_STATUSES:
                        old_status = old.get("status")
                        task.update({
                            "status": old_status,
                            "detail": old.get("detail") or ("Нет доступа" if old_status == "limited" else "Готово"),
                            "progress_pct": 100,
                            "progress_text": "Нет доступа" if old_status == "limited" else "100%",
                        })
                if not scope and not task_scope and all(
                    task.get("status") in TERMINAL_TASK_STATUSES for task in fresh_tasks
                ):
                    raise ValueError("Незавершённых этапов нет")
            self.stop_requested = False
            self.paused_clients = set()
            self.replay_clients = set()
            self.resume_task_ids = {
                task["id"]
                for task in fresh_tasks
                if resume
                and (not scope or task.get("client") in scope)
                and (not task_scope or task["id"] in task_scope)
                and task.get("status") not in TERMINAL_TASK_STATUSES
            }
            self.state = {
                "ok": True,
                "run_id": str(uuid.uuid4()),
                "status": "running",
                "message": "Обновление всех аккаунтов выполняется",
                "started_at": _now(),
                "updated_at": _now(),
                "finished_at": "",
                "elapsed_seconds": 0,
                "current": None,
                "clients": clients,
                "rows": rows,
                "tasks": fresh_tasks,
                "scope_clients": sorted(scope),
                "scope_task_ids": sorted(task_scope),
                "paused_clients": [],
                "logs": list(self.state.get("logs") or [])[-200:] if resume else [],
            }
            if task_scope:
                note = (
                    f"{'Продолжение' if resume else 'Запуск'} выбранных этапов: {len(task_scope)}"
                )
            elif scope:
                scope_labels = ", ".join(
                    sorted({
                        task["client_label"] for task in fresh_tasks if task.get("client") in scope
                    })
                )
                note = (
                    f"{'Продолжение' if resume else 'Запуск'} только по аккаунту: {scope_labels}"
                )
            else:
                note = "Продолжение общего процесса" if resume else "Запуск обновления всех аккаунтов"
            self._append_log_locked(note, "start")
            self._save_locked(force=True)
            self.thread = threading.Thread(target=self._run, args=(plan,), name="admin-all-clients-daily", daemon=True)
            self.thread.start()
            return self.snapshot()

    def stop(self):
        with self.lock:
            if self.state.get("status") not in {"running", "stopping"}:
                return self.snapshot()
            self.stop_requested = True
            worker = self.thread
            if worker is None or not worker.is_alive():
                # поток уже мёртв (например, упал внутри рантайма) — не оставляем вечное «stopping»
                self.state["status"] = "stopped"
                self.state["message"] = "Прогон остановлен: рабочий поток не активен"
                self._append_log_locked(self.state["message"], "stopped")
                self._finish_locked()
                return self.snapshot()
            self.state["status"] = "stopping"
            self.state["message"] = "Останавливается текущий скрипт"
            self._append_log_locked(
                "Запрошена остановка выполняющихся скриптов и оставшейся очереди", "stopped"
            )
            processes = list(self.processes.values())
            if not processes and self.process is not None:
                processes = [self.process]
            self._save_locked(force=True)
        for process in processes:
            if process is None or process.poll() is not None:
                continue
            if self.terminate_process:
                self.terminate_process(process)
            else:
                process.terminate()
        return self.snapshot()

    def _client_of(self, task_id):
        for task in self.state.get("tasks") or []:
            if task.get("id") == task_id:
                return task.get("client") or ""
        return ""

    def stop_client(self, client):
        """Снимает аккаунт с прогона и гасит его текущий скрипт."""
        client = str(client or "")
        victims = []
        with self.lock:
            label = next(
                (
                    task.get("client_label")
                    for task in self.state.get("tasks") or []
                    if task.get("client") == client
                ),
                client,
            )
            self.paused_clients.add(client)
            self.replay_clients.discard(client)
            self.state["paused_clients"] = sorted(self.paused_clients)
            for task_id, process in list(self.processes.items()):
                if self._client_of(task_id) == client:
                    victims.append((task_id, process))
            for task_id, _process in victims:
                self._set_task_locked(
                    task_id,
                    status="stopped",
                    detail="Остановлено по аккаунту",
                    progress_text="Остановлено",
                )
            self._append_log_locked(f"Аккаунт снят с прогона: {label}", "stopped")
            self._save_locked(force=True)
        for _task_id, process in victims:
            if process is None or process.poll() is not None:
                continue
            if self.terminate_process:
                self.terminate_process(process)
            else:
                process.terminate()
        return self.snapshot()

    def resume_client(self, client, task_ids=None):
        """Возвращает аккаунт в прогон; если прогона нет — запускает только его."""
        client = str(client or "")
        with self.lock:
            running = self.state.get("status") in {"running", "stopping"}
            if running:
                label = next(
                    (
                        task.get("client_label")
                        for task in self.state.get("tasks") or []
                        if task.get("client") == client
                    ),
                    client,
                )
                self.paused_clients.discard(client)
                self.state["paused_clients"] = sorted(self.paused_clients)
                self.replay_clients.add(client)
                selected = {str(value) for value in (task_ids or []) if str(value or "").strip()}
                self.resume_task_ids.update(
                    task["id"]
                    for task in self.state.get("tasks") or []
                    if task.get("client") == client
                    and task.get("status") not in TERMINAL_TASK_STATUSES
                    and (not selected or task.get("id") in selected)
                )
                self._append_log_locked(
                    f"Аккаунт возвращён в прогон, его этапы будут добраны в конце: {label}",
                    "start",
                )
                self._save_locked(force=True)
                return self.snapshot()
        return self.start(resume=True, only_clients=[client], only_task_ids=task_ids)

    def start_client(self, client, task_ids=None):
        return self.start(resume=False, only_clients=[client], only_task_ids=task_ids)

    def _set_task_locked(self, task_id, **values):
        for task in self.state.get("tasks") or []:
            if task.get("id") == task_id:
                task.update(values)
                return task
        return None

    def _update_progress_locked(self, task_id, line):
        values = {}
        match = PROGRESS_RE.search(line)
        if match:
            values["progress_pct"] = min(100, max(0, float(match.group(3).replace(",", "."))))
            values["progress_text"] = f"{match.group(1)}/{match.group(2)} · {match.group(3)}%"
        rows_match = ROWS_RE.search(line)
        if rows_match:
            current = re.sub(r"\s+", "", rows_match.group(1) or "")
            total = re.sub(r"\s+", "", rows_match.group(2) or "")
            values["rows_text"] = f"строки {current} / {total or '?'}"
        if values:
            self._set_task_locked(task_id, **values)

    @staticmethod
    def _failure_policy(task):
        """('skip' | 'retry:{n}:{backoff}' | 'fatal') -> (retries, backoff_seconds, fatal)."""
        raw = str((task or {}).get("failure_policy") or DEFAULT_FAILURE_POLICY).strip().casefold()
        if raw == "fatal":
            return 0, 0, True
        if raw.startswith("retry"):
            parts = raw.split(":")
            retries = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            backoff = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else DEFAULT_RETRY_BACKOFF_SECONDS
            return max(0, retries), max(0, backoff), False
        return 0, 0, False

    def _interruptible_sleep(self, seconds, label):
        """Пауза между повторными проходами, прерываемая кнопкой «Остановить»."""
        total = max(0, int(seconds))
        with self.lock:
            self._append_log_locked(label, "warning")
            self._save_locked(force=True)
        deadline = time.monotonic() + total
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if self.stop_requested:
                return False
            time.sleep(min(1.0, remaining))

    def _execute_task(self, task_id, internal_tasks, attempt=1):
        """Выполняет один этап. Возвращает done | limited | failed | stopped | skipped."""
        with self.lock:
            public_task = next(
                (task for task in self.state.get("tasks") or [] if task.get("id") == task_id),
                None,
            )
            if public_task is None or public_task.get("status") in TERMINAL_TASK_STATUSES:
                return "skipped"
            if self.stop_requested:
                return "stopped"
            task = internal_tasks.get(task_id)
            if not task:
                self._set_task_locked(
                    task_id,
                    status="error",
                    detail="Этап больше не зарегистрирован",
                    progress_text="Ошибка",
                    attempts=attempt,
                )
                self._append_log_locked(f"Этап {task_id} больше не зарегистрирован — пропущен", "error")
                self._save_locked(force=True)
                return "failed"
            use_resume = bool(task.get("supports_resume")) and (
                attempt > 1 or task_id in self.resume_task_ids
            )
            current = {
                "task_id": task_id,
                "client": task["client"],
                "client_label": task["client_label"],
                "key": task["key"],
                "report": task["report"],
            }
            self.state["current"] = current
            active = [
                row for row in (self.state.get("active") or [])
                if row.get("task_id") != task_id
            ]
            active.append(current)
            self.state["active"] = active
            self._set_task_locked(
                task_id,
                status="running",
                detail="Выполняется",
                progress_text="В работе",
                attempts=attempt,
            )
            start_note = f"> {task['client_label']} · {task['report']}"
            if use_resume:
                start_note += " | checkpoint"
            if attempt > 1:
                start_note += f" | повтор {attempt}"
            self._append_log_locked(start_note, "start")
            self._save_locked(force=True)

        acquired = True
        acquire_detail = ""
        if self.acquire_task:
            acquired, acquire_detail = self.acquire_task(task)
        if not acquired:
            with self.lock:
                detail = acquire_detail or "Этап уже запущен"
                self._set_task_locked(
                    task_id, status="error", detail=detail, progress_text="Ошибка", attempts=attempt
                )
                self._append_log_locked(
                    f"Не удалось запустить: {task['client_label']} · {task['report']} · {detail}", "error"
                )
                self.state["active"] = [
                    row for row in (self.state.get("active") or [])
                    if row.get("task_id") != task_id
                ]
                self.state["current"] = (self.state["active"] or [None])[-1]
                self._save_locked(force=True)
            return "failed"

        nonfatal_patterns = [
            str(value or "").casefold()
            for value in task.get("nonfatal_error_patterns") or []
            if str(value or "").strip()
        ]
        matched_nonfatal = False
        returncode = None
        error_text = ""
        process = None
        try:
            command = list(task["command"])
            if use_resume and "--resume" not in command:
                command.append("--resume")
            process = subprocess.Popen(
                command,
                cwd=task.get("cwd"),
                env=task.get("env"),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **self.process_group_kwargs(),
            )
            with self.lock:
                self.process = process
                self.processes[task_id] = process
            if self.register_process:
                self.register_process(task, process)
            if process.stdout:
                for raw_line in iter(process.stdout.readline, ""):
                    line = raw_line.rstrip("\r\n")
                    if nonfatal_patterns and not matched_nonfatal:
                        folded = line.casefold()
                        if any(pattern in folded for pattern in nonfatal_patterns):
                            matched_nonfatal = True
                    stored = f"[{task['client_label']}] {line}" if self.max_workers > 1 else line
                    with self.lock:
                        self._append_log_locked(stored, "output")
                        self._update_progress_locked(task_id, line)
                        self._save_locked()
            returncode = process.wait()
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
        finally:
            if process is not None and process.stdout:
                process.stdout.close()
            with self.lock:
                self.processes.pop(task_id, None)
                self.process = next(iter(self.processes.values()), None)
            if self.release_task:
                self.release_task(task)

        with self.lock:
            self.state["active"] = [
                row for row in (self.state.get("active") or [])
                if row.get("task_id") != task_id
            ]
            self.state["current"] = (self.state["active"] or [None])[-1]
            if self.stop_requested:
                self._set_task_locked(
                    task_id, status="stopped", detail="Остановлено пользователем", progress_text="Остановлено"
                )
                self.state["status"] = "stopped"
                self.state["message"] = f"Остановлено: {task['client_label']} · {task['report']}"
                self._append_log_locked(self.state["message"], "stopped")
                self._save_locked(force=True)
                return "stopped"
            failed = bool(error_text) or returncode != 0
            if matched_nonfatal and (failed or task.get("nonfatal_on_success")):
                detail = str(task.get("nonfatal_status_label") or "Источник недоступен")
                self._set_task_locked(
                    task_id, status="limited", detail=detail, progress_pct=100, progress_text=detail
                )
                self._append_log_locked(
                    f"Пропуск без остановки: {task['client_label']} · {task['report']} · {detail}", "warning"
                )
                self._save_locked(force=True)
                return "limited"
            if failed:
                detail = error_text or f"Скрипт завершился с кодом {returncode}"
                self._set_task_locked(
                    task_id, status="error", detail=detail, progress_text="Ошибка", attempts=attempt
                )
                self._append_log_locked(
                    f"Ошибка: {task['client_label']} · {task['report']}: {detail}", "error"
                )
                self._save_locked(force=True)
                return "failed"
            self._set_task_locked(
                task_id, status="ok", detail="Готово", progress_pct=100, progress_text="100%"
            )
            self._append_log_locked(f"Готово: {task['client_label']} · {task['report']}", "done")
            self._save_locked(force=True)
            return "done"

    def _register_outcome(self, outcome, task_id, internal_tasks, attempt, retry_queue):
        """Учитывает результат этапа. False — прогон дальше идти не должен."""
        if outcome in {"done", "limited"}:
            self._consecutive_failures = 0
            return True
        if outcome == "skipped":
            return True
        self._consecutive_failures += 1
        retries, backoff, fatal = self._failure_policy(internal_tasks.get(task_id))
        if fatal:
            with self.lock:
                self.state["status"] = "error"
                self.state["message"] = f"Критический отказ этапа: {task_id}"
                self._append_log_locked(self.state["message"], "error")
                self._save_locked(force=True)
            return False
        if self._consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            with self.lock:
                self.state["status"] = "error"
                self.state["message"] = (
                    f"Отказов подряд: {self._consecutive_failures} — прогон остановлен, "
                    "чтобы не жечь квоты внешних API"
                )
                self._append_log_locked(self.state["message"], "error")
                self._save_locked(force=True)
            return False
        if attempt <= retries:
            retry_queue.append((task_id, attempt + 1, max(1, backoff * attempt)))
            with self.lock:
                self._append_log_locked(
                    f"Этап {task_id} поставлен на повтор {attempt + 1}/{retries + 1}", "warning"
                )
                self._save_locked(force=True)
        return True

    def _client_chains(self, only_clients=None):
        """Незавершённые этапы, сгруппированные в независимые цепочки аккаунтов.

        Порядок внутри аккаунта совпадает с визуальным порядком матрицы. Поэтому
        каждый аккаунт сразу начинает свой первый доступный этап и затем идёт
        сверху вниз, не ожидая отсутствующие или долгие этапы других аккаунтов.
        """
        chains = {}
        order = []
        allowed = {str(value) for value in (only_clients or []) if str(value or "").strip()}
        scope = set(self.state.get("scope_clients") or [])
        task_scope = set(self.state.get("scope_task_ids") or [])
        for task in self.state.get("tasks") or []:
            if task.get("status") in TERMINAL_TASK_STATUSES:
                continue
            client = task.get("client") or ""
            if client in self.paused_clients:
                continue
            if scope and client not in scope:
                continue
            if task_scope and task.get("id") not in task_scope:
                continue
            if allowed and client not in allowed:
                continue
            if client not in chains:
                chains[client] = []
                order.append(client)
            chains[client].append(task["id"])
        # Длинные цепочки стартуют первыми, но слот получает каждый аккаунт целиком.
        order.sort(key=lambda client: -len(chains[client]))
        return [(client, chains[client]) for client in order]

    def _run_client_chains(self, internal_tasks, retry_queue, only_clients=None):
        """Гоняет независимую последовательную цепочку для каждого аккаунта."""
        chains = self._client_chains(only_clients=only_clients)
        if not chains:
            return True
        workers = max(1, min(self.max_workers, len(chains)))
        with self.lock:
            self._append_log_locked(
                f"> Параллельный запуск: аккаунтов {len(chains)} | одновременно до {workers}"
                f" | перестроек витрин до {self.max_view_workers}",
                "start",
            )
            self._save_locked(force=True)
        slots = threading.Semaphore(workers)
        view_slots = threading.Semaphore(self.max_view_workers)
        heavy_slots = threading.Semaphore(HEAVY_CLIENT_LIMIT)
        aborted = threading.Event()
        crashes = []

        def run_chain(client, task_ids):
            try:
                with slots:
                    for task_id in task_ids:
                        if self.stop_requested or aborted.is_set():
                            return
                        with self.lock:
                            if client in self.paused_clients:
                                return
                        task = internal_tasks.get(task_id) or {}
                        is_view = task.get("stage") == "views"
                        is_heavy_view = is_view and client in HEAVY_CLIENTS
                        if is_heavy_view:
                            heavy_slots.acquire()
                        if is_view:
                            view_slots.acquire()
                        try:
                            outcome = self._execute_task(task_id, internal_tasks, attempt=1)
                        finally:
                            if is_view:
                                view_slots.release()
                            if is_heavy_view:
                                heavy_slots.release()
                        if outcome == "stopped":
                            aborted.set()
                            return
                        with self.lock:
                            if not self._register_outcome(
                                outcome, task_id, internal_tasks, 1, retry_queue
                            ):
                                aborted.set()
                                return
            except Exception as exc:  # noqa: BLE001 - падение воркера нельзя терять
                crashes.append(exc)
                aborted.set()

        threads = [
            threading.Thread(
                target=run_chain, args=(client, task_ids), name=f"admin-daily-{client}", daemon=True
            )
            for client, task_ids in chains
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if crashes:
            raise crashes[0]
        return not aborted.is_set() and not self.stop_requested

    def _run(self, plan):
        internal_tasks = {task["id"]: task for task in plan.get("tasks") or []}
        try:
            self._consecutive_failures = 0
            retry_queue = []
            if not self._run_client_chains(internal_tasks, retry_queue):
                return
            # Аккаунты, возвращённые в прогон после снятия, добираются отдельным
            # проходом без ожидания или повторного запуска остальных аккаунтов.
            for _catch_up in range(CATCH_UP_PASS_LIMIT):
                with self.lock:
                    pending = sorted(self.replay_clients)
                    self.replay_clients = set()
                if not pending or self.stop_requested:
                    break
                with self.lock:
                    self._append_log_locked(
                        f"Догоняющий проход по аккаунтам: {', '.join(pending)}", "start"
                    )
                    self._save_locked(force=True)
                if not self._run_client_chains(
                    internal_tasks, retry_queue, only_clients=pending
                ):
                    return
            round_number = 0
            while retry_queue and not self.stop_requested:
                round_number += 1
                delay = max(item[2] for item in retry_queue)
                if not self._interruptible_sleep(
                    delay,
                    f"Повторный проход {round_number}: этапов с ошибкой {len(retry_queue)} | пауза {delay} с",
                ):
                    break
                current_round, retry_queue = retry_queue, []
                for task_id, attempt, _backoff in current_round:
                    if self.stop_requested:
                        break
                    outcome = self._execute_task(task_id, internal_tasks, attempt=attempt)
                    if outcome == "stopped":
                        return
                    if not self._register_outcome(outcome, task_id, internal_tasks, attempt, retry_queue):
                        return
        except Exception as exc:
            with self.lock:
                self.state["status"] = "error"
                self.state["message"] = f"Сбой оркестратора: {type(exc).__name__}: {exc}"
                self._append_log_locked(self.state["message"], "error")
        finally:
            with self.lock:
                self._finish_run_locked()

    def _finish_run_locked(self):
        tasks = self.state.get("tasks") or []
        evaluated_tasks = self._tasks_in_run_scope(self.state, tasks)
        limited_count = sum(1 for task in evaluated_tasks if task.get("status") == "limited")
        failed_count = sum(1 for task in evaluated_tasks if task.get("status") == "error")
        if self.stop_requested and self.state.get("status") != "stopped":
            self.state["status"] = "stopped"
            self.state["message"] = "Общий процесс остановлен"
            self._append_log_locked(self.state["message"], "stopped")
        elif self.state.get("status") not in {"stopped", "error"}:
            if failed_count:
                self.state["status"] = "error"
                self.state["message"] = f"Прогон завершён; этапов с ошибкой: {failed_count}"
                if limited_count:
                    self.state["message"] += f"; источников без доступа: {limited_count}"
                self._append_log_locked(self.state["message"], "error")
            else:
                self.state["status"] = "completed"
                self.state["message"] = (
                    f"Обновление завершено; источников без доступа: {limited_count}"
                    if limited_count
                    else "Все подключённые ежедневные процессы завершены"
                )
                self._append_log_locked(self.state["message"], "done")
        self._finish_locked()

    def _finish_locked(self):
        self.state["finished_at"] = _now()
        self.state["elapsed_seconds"] = _elapsed_seconds(self.state.get("started_at"))
        self.state["current"] = None
        self.state["active"] = []
        self.processes = {}
        self.process = None
        self.stop_requested = False
        self._save_locked(force=True)
