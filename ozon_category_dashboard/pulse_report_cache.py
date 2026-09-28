"""Small stale-while-revalidate cache for expensive read-only PULSE reports.

The VPS reader serves immutable analytical snapshots and is explicitly marked
with ``PULSE_READ_ONLY_REPORT_CACHE=1``.  Keeping the last successful payload
on the mounted data volume makes navigation fast even after a container
restart, while an expired entry is refreshed in the background.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()
_KEY_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_REFRESHING: set[tuple[str, str]] = set()


def _enabled() -> bool:
    return os.environ.get("PULSE_READ_ONLY_REPORT_CACHE") == "1"


def _ttl_seconds() -> float:
    try:
        return max(30.0, float(os.environ.get("PULSE_REPORT_CACHE_TTL_SECONDS", "900")))
    except ValueError:
        return 900.0


def _cache_dir() -> Path:
    return Path(os.environ.get("PULSE_REPORT_CACHE_DIR", "/var/lib/pulse/report_cache"))


def _path(namespace: str, raw_key: str) -> Path:
    digest = hashlib.sha256(f"{namespace}\n{raw_key}".encode("utf-8")).hexdigest()
    return _cache_dir() / f"{namespace}-{digest}.json"


def _load_disk(namespace: str, raw_key: str) -> tuple[float, dict[str, Any]] | None:
    path = _path(namespace, raw_key)
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope.get("payload")
        stored_at = float(envelope.get("stored_at") or path.stat().st_mtime)
        if isinstance(payload, dict):
            age = max(0.0, time.time() - stored_at)
            return time.monotonic() - age, payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def _store(key: tuple[str, str], payload: dict[str, Any]) -> None:
    stored_at = time.time()
    with _LOCK:
        _CACHE[key] = (time.monotonic(), payload)
    namespace, raw_key = key
    path = _path(namespace, raw_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        temp.write_text(
            json.dumps({"stored_at": stored_at, "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp, path)
    except (OSError, TypeError, ValueError) as exc:
        print(f"PULSE report cache persist failed: {namespace}: {type(exc).__name__}", flush=True)


def _refresh(
    key: tuple[str, str], loader: Callable[[], dict[str, Any]], context: contextvars.Context
) -> None:
    namespace = key[0]
    started = time.monotonic()
    print(f"ПЛАН: обновление кэша отчёта 1 | {namespace} | HTTP отвечает старым снимком", flush=True)
    try:
        payload = context.run(loader)
        _store(key, payload)
        print(
            f"ИТОГ: кэш {namespace} обновлён | ошибок 0 | время {time.monotonic() - started:.1f}s | partial=false",
            flush=True,
        )
    except Exception as exc:
        print(
            f"ИТОГ: кэш {namespace} не обновлён | ошибок 1 ({type(exc).__name__}) | "
            f"время {time.monotonic() - started:.1f}s | partial=true",
            flush=True,
        )
    finally:
        with _LOCK:
            _REFRESHING.discard(key)


def cached_payload(
    namespace: str,
    raw_key: str,
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Return a cached payload; expired successful data refreshes asynchronously."""
    if not _enabled():
        return loader()
    key = (namespace, raw_key)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is None:
        cached = _load_disk(namespace, raw_key)
        if cached is not None:
            with _LOCK:
                _CACHE[key] = cached
    now = time.monotonic()
    if cached and now - cached[0] <= _ttl_seconds():
        return cached[1]
    if cached:
        with _LOCK:
            if key not in _REFRESHING:
                _REFRESHING.add(key)
                context = contextvars.copy_context()
                threading.Thread(
                    target=_refresh,
                    args=(key, loader, context),
                    name=f"pulse-cache-{namespace}",
                    daemon=True,
                ).start()
        return cached[1]
    with _LOCK:
        key_lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with key_lock:
        with _LOCK:
            cached = _CACHE.get(key)
        if cached:
            return cached[1]
        started = time.monotonic()
        print(f"ПЛАН: холодный кэш отчёта 1 | {namespace} | ожидание первого расчёта", flush=True)
        payload = loader()
        _store(key, payload)
        print(
            f"ИТОГ: кэш {namespace} создан | ошибок 0 | время {time.monotonic() - started:.1f}s | partial=false",
            flush=True,
        )
        return payload
