"""Secret-safe provider selector for Health Check conclusions.

Only the loopback local model is callable by default. External providers stay
visible in the UI but require a separate, explicit data-egress decision before
their adapters are enabled.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs


VERSION = "1.0.0"
PROVIDERS = {
    "local": "Локальная модель",
    "openrouter": "OpenRouter",
    "codex": "Codex",
    "claude": "Claude",
}
EXTERNAL_PROVIDERS = frozenset({"openrouter", "codex", "claude"})


def call(app: Any, provider: str, prompt: str) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        return {"ok": False, "status": "invalid", "message": "Неизвестный провайдер модели."}
    if provider in EXTERNAL_PROVIDERS:
        return {
            "ok": False,
            "status": "authorization_required",
            "provider": provider,
            "message": (
                f"{PROVIDERS[provider]} выбран, но внешняя передача клиентской аналитики не включена. "
                "Нужно отдельно разрешить этот канал и определить набор передаваемых данных."
            ),
        }
    local_call = getattr(app, "call_lm_studio", None)
    if not callable(local_call):
        return {"ok": False, "status": "not_configured", "provider": provider, "message": "Локальная модель не подключена к BI runtime."}
    result = dict(local_call(prompt))
    answer = str(result.get("text") or "").strip()
    if result.get("ok") and answer:
        return {"ok": True, "status": "completed", "provider": provider, "model": str(result.get("model") or "local-model"), "text": answer, "message": ""}
    return {"ok": False, "status": "unavailable", "provider": provider, "model": str(result.get("model") or ""), "message": "Локальная модель недоступна; базовый вывод по правилам сохранён."}


def install(app: Any, runtime: Any) -> None:
    if getattr(runtime, "_KOKOC_MODEL_SELECTOR_INSTALLED", False):
        return
    original = runtime.handle_hypothesis_analysis

    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = dict(original(*args, **kwargs))
        parsed = args[1] if len(args) > 1 else kwargs.get("parsed")
        params = parse_qs(str(getattr(parsed, "query", "") or ""), keep_blank_values=False)
        provider = str((params.get("model_provider") or [""])[0]).strip().lower()
        if not provider:
            return result
        if provider not in PROVIDERS:
            raise runtime.PlanFactMatrixRequestError("Неизвестный model_provider.")
        root = kwargs.get("project_root")
        hypothesis_module = runtime.load_hypothesis_module(root)
        prompt = hypothesis_module.build_model_prompt(result)
        model_result = call(app, provider, prompt)
        engine = result.setdefault("engine", {})
        engine["model_provider"] = provider
        engine["model_provider_label"] = PROVIDERS[provider]
        engine["model_status"] = str(model_result.get("status") or "unavailable")
        engine["model"] = str(model_result.get("model") or "")
        engine["model_message"] = str(model_result.get("message") or "")
        if model_result.get("ok") and str(model_result.get("text") or "").strip():
            result["model_conclusion"] = str(model_result["text"]).strip()
        else:
            result.pop("model_conclusion", None)
        return result

    runtime.handle_hypothesis_analysis = wrapped
    runtime._KOKOC_MODEL_SELECTOR_INSTALLED = True
