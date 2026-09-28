"""HTTP/runtime adapter for the read-only plan/fact funnel matrix.

This module is intentionally small.  It belongs to the BI launcher layer and
delegates all metric calculations to
``scripts.wb_screening.plan_fact_matrix.build_live_matrix`` in the main
workspace.

The adapter makes PostgreSQL connections read-only only inside the current
matrix request.  A ``ContextVar`` is used so concurrent BI requests that need
write access are not affected.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs


DEFAULT_PROJECT_ROOT = Path(r"C:\Users\Solod\Documents\Память _Прорыв_")
PROJECT_ROOT_ENV = "WB_SCREENING_PROJECT_ROOT"
MATRIX_MODULE_RELATIVE_PATH = Path("scripts") / "wb_screening" / "plan_fact_matrix.py"
HYPOTHESIS_MODULE_RELATIVE_PATH = (
    Path("scripts") / "wb_screening" / "health_check_hypotheses.py"
)
SUPPORTED_MARKETPLACES = frozenset({"wb", "ozon"})
MAX_QUERY_VALUE_LENGTH = 128

_READ_ONLY_REQUEST: ContextVar[bool] = ContextVar(
    "planfact_matrix_read_only_request",
    default=False,
)
_INSTALL_LOCK = threading.RLock()
_MODULE_LOCK = threading.RLock()
_MODULE_CACHE: dict[Path, ModuleType] = {}


class PlanFactMatrixRequestError(ValueError):
    """A safe, user-facing 400-level request validation error."""


def _first(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key) or []
    if not values:
        return None
    value = str(values[0]).strip()
    if not value:
        return None
    if len(value) > MAX_QUERY_VALUE_LENGTH:
        raise PlanFactMatrixRequestError(f"Параметр {key} слишком длинный.")
    return value


def _parse_iso_date(value: str | None, key: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PlanFactMatrixRequestError(
            f"Параметр {key} должен быть датой YYYY-MM-DD."
        ) from exc


def _parse_month(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise PlanFactMatrixRequestError(
            "Параметр month должен быть месяцем YYYY-MM."
        )
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise PlanFactMatrixRequestError(
            "Параметр month должен быть существующим месяцем YYYY-MM."
        ) from exc
    return value


def _read_only_options(options: object) -> str:
    current = str(options or "").strip()
    additions: list[str] = []
    if "default_transaction_read_only" not in current:
        additions.append("-c default_transaction_read_only=on")
    if "statement_timeout" not in current:
        additions.append("-c statement_timeout=30000")
    return " ".join(part for part in (current, *additions) if part).strip()


def install_request_scoped_read_only_config(app: Any) -> None:
    """Install an idempotent, request-scoped wrapper around ``read_db_config``."""

    marker = "_planfact_matrix_read_only_config_installed"
    with _INSTALL_LOCK:
        if getattr(app, marker, False):
            return
        original = getattr(app, "read_db_config", None)
        if not callable(original):
            raise RuntimeError("BI app.read_db_config недоступен.")

        def scoped_read_db_config(client: str | None = None) -> dict[str, Any]:
            config = dict(original(client))
            if _READ_ONLY_REQUEST.get():
                config["options"] = _read_only_options(config.get("options"))
            return config

        scoped_read_db_config.__name__ = "planfactMatrixScopedReadDbConfig"
        setattr(app, "_planfact_matrix_original_read_db_config", original)
        setattr(app, "read_db_config", scoped_read_db_config)
        setattr(app, marker, True)


def _project_root(value: str | Path | None) -> Path:
    raw = value or os.environ.get(PROJECT_ROOT_ENV) or DEFAULT_PROJECT_ROOT
    root = Path(raw).expanduser().resolve()
    source = root / MATRIX_MODULE_RELATIVE_PATH
    if not source.is_file():
        raise RuntimeError(f"Не найден модуль матрицы: {source}")
    return root


def load_matrix_module(project_root: str | Path | None = None) -> ModuleType:
    """Load the metric builder from one exact, launcher-controlled workspace."""

    root = _project_root(project_root)
    source = (root / MATRIX_MODULE_RELATIVE_PATH).resolve()
    with _MODULE_LOCK:
        cached = _MODULE_CACHE.get(source)
        if cached is not None:
            return cached

        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        module_name = "_wb_plan_fact_matrix_runtime_target"
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise RuntimeError("Не удалось подготовить модуль матрицы.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        builder = getattr(module, "build_live_matrix", None)
        if not callable(builder):
            raise RuntimeError("build_live_matrix не экспортирован модулем матрицы.")
        _MODULE_CACHE[source] = module
        return module


def load_hypothesis_module(project_root: str | Path | None = None) -> ModuleType:
    """Load the bounded diagnostic engine from the same controlled workspace."""

    root = _project_root(project_root)
    source = (root / HYPOTHESIS_MODULE_RELATIVE_PATH).resolve()
    if not source.is_file():
        raise RuntimeError(f"Не найден модуль анализа гипотез: {source}")
    with _MODULE_LOCK:
        cached = _MODULE_CACHE.get(source)
        if cached is not None:
            return cached
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        module_name = "_wb_health_check_hypotheses_runtime_target"
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise RuntimeError("Не удалось подготовить модуль анализа гипотез.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if not callable(getattr(module, "analyze_metric", None)):
            raise RuntimeError("analyze_metric не экспортирован модулем диагностики.")
        _MODULE_CACHE[source] = module
        return module


def handle(
    app: Any,
    parsed: Any,
    *,
    project_root: str | Path | None = None,
    builder: Callable[..., Mapping[str, Any]] | None = None,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    """Build one matrix response without writes or marketplace API calls."""

    params = parse_qs(str(getattr(parsed, "query", "") or ""), keep_blank_values=False)
    client = app.client_from_query(str(getattr(parsed, "query", "") or ""))
    marketplace = (
        _first(params, "marketplace")
        or app.marketplace_from_query(str(getattr(parsed, "query", "") or ""))
    ).lower()
    if marketplace not in SUPPORTED_MARKETPLACES:
        raise PlanFactMatrixRequestError(
            "Health Chek поддерживает marketplace=wb или marketplace=ozon."
        )
    supported_for_client = getattr(app, "client_marketplace_ids", None)
    if callable(supported_for_client):
        client_marketplaces = {
            str(item).strip().lower()
            for item in supported_for_client(client)
        }
        if marketplace not in client_marketplaces:
            raise PlanFactMatrixRequestError(
                f"Площадка {marketplace.upper()} не подключена для выбранного клиента."
            )

    date_from = _parse_iso_date(_first(params, "date_from"), "date_from")
    date_to = _parse_iso_date(_first(params, "date_to"), "date_to")
    month = _parse_month(_first(params, "month"))
    fast_home = _first(params, "home") == "1"
    filters = {
        key: value
        for key in ("category", "product", "article")
        if (value := _first(params, key)) is not None
    }
    if date_from and date_to and date_from > date_to:
        raise PlanFactMatrixRequestError("date_from не может быть позже date_to.")

    root = _project_root(project_root)
    if builder is None:
        builder = getattr(load_matrix_module(root), "build_live_matrix")

    install_request_scoped_read_only_config(app)
    client_token = app.CURRENT_CLIENT.set(client)
    read_only_token = _READ_ONLY_REQUEST.set(True)
    try:
        payload = builder(
            app,
            client,
            marketplace,
            date_from=date_from,
            date_to=date_to,
            project_root=root,
            month=month,
            filters=filters,
            include_diagnostics=include_diagnostics,
            prefer_source_planfact=fast_home,
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("Матрица вернула некорректный payload.")
        return dict(payload)
    finally:
        _READ_ONLY_REQUEST.reset(read_only_token)
        app.CURRENT_CLIENT.reset(client_token)


def handle_hypothesis_analysis(
    app: Any,
    parsed: Any,
    *,
    project_root: str | Path | None = None,
    builder: Callable[..., Mapping[str, Any]] | None = None,
    analyzer: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a fresh read-only matrix and diagnose one declared metric."""

    params = parse_qs(str(getattr(parsed, "query", "") or ""), keep_blank_values=False)
    metric_id = _first(params, "metric")
    if not metric_id or not re.fullmatch(r"[a-z0-9_]{2,64}", metric_id):
        raise PlanFactMatrixRequestError(
            "Параметр metric должен содержать ID метрики Health Check."
        )
    root = _project_root(project_root)
    payload = handle(
        app,
        parsed,
        project_root=root,
        builder=builder,
        include_diagnostics=True,
    )
    hypothesis_module = load_hypothesis_module(root)
    if analyzer is None:
        analyzer = getattr(hypothesis_module, "analyze_metric")
    result = dict(analyzer(payload, metric_id))
    result["context"] = {
        "client": payload.get("client"),
        "marketplace": payload.get("marketplace"),
        "analysis_date": payload.get("analysis_date"),
        "month": payload.get("month"),
        "scope": payload.get("scope"),
    }

    model_enabled = str(os.environ.get("HEALTH_CHECK_MODEL_ENABLED") or "").lower() in {
        "1", "true", "yes", "on"
    }
    call_model = getattr(app, "call_lm_studio", None)
    if model_enabled and callable(call_model):
        prompt_builder = getattr(hypothesis_module, "build_model_prompt")
        model_result = dict(call_model(prompt_builder(result)))
        if model_result.get("ok") and str(model_result.get("text") or "").strip():
            result["engine"]["model_status"] = "completed"
            result["engine"]["model"] = str(model_result.get("model") or "configured-model")
            result["model_conclusion"] = str(model_result.get("text") or "").strip()
        else:
            result["engine"]["model_status"] = "unavailable"
            result["engine"]["model_message"] = (
                "Модель недоступна; показан детерминированный анализ по DAG."
            )
    else:
        result["engine"]["model_status"] = "not_configured"
        result["engine"]["model_message"] = (
            "Live-модель не подключена; показан детерминированный анализ по DAG."
        )
    return result

