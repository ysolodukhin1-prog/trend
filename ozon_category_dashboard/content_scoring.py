"""Evidence-first marketplace content scoring for a single SKU card."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ContentScoringError(RuntimeError):
    pass


SCORING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "overall_score", "confidence_pct", "verdict", "executive_summary", "strengths",
        "critical_gaps", "dimensions", "funnel_coverage", "image_reviews", "evidence",
        "prioritized_actions", "ab_tests", "data_gaps",
    ],
    "properties": {
        "overall_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["strong", "workable", "weak", "critical"]},
        "executive_summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "critical_gaps": {"type": "array", "items": {"type": "string"}},
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "label", "score", "max_score", "status", "rationale", "evidence_refs"],
                "properties": {
                    "id": {"type": "string"}, "label": {"type": "string"},
                    "score": {"type": "number", "minimum": 0}, "max_score": {"type": "number", "minimum": 1},
                    "status": {"type": "string", "enum": ["strong", "partial", "weak", "missing", "not_observable"]},
                    "rationale": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "funnel_coverage": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["stage", "status", "evidence_refs", "gap"],
                "properties": {
                    "stage": {"type": "string"},
                    "status": {"type": "string", "enum": ["covered", "partial", "missing", "not_observable"]},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "gap": {"type": "string"},
                },
            },
        },
        "image_reviews": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "image_index", "role", "funnel_stage", "score", "visual_quality", "text_legibility",
                    "benefit", "proof", "problem", "recommendation", "evidence",
                ],
                "properties": {
                    "image_index": {"type": "integer", "minimum": 1}, "role": {"type": "string"},
                    "funnel_stage": {"type": "string"}, "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "visual_quality": {"type": "string"}, "text_legibility": {"type": "string"},
                    "benefit": {"type": "string"}, "proof": {"type": "string"},
                    "problem": {"type": "string"}, "recommendation": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "type", "source_ref", "observation", "impact"],
                "properties": {
                    "id": {"type": "string"}, "type": {"type": "string"}, "source_ref": {"type": "string"},
                    "observation": {"type": "string"}, "impact": {"type": "string"},
                },
            },
        },
        "prioritized_actions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["priority", "action", "why", "evidence_refs", "expected_effect", "effort"],
                "properties": {
                    "priority": {"type": "integer", "minimum": 1}, "action": {"type": "string"},
                    "why": {"type": "string"}, "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "expected_effect": {"type": "string"}, "effort": {"type": "string", "enum": ["low", "medium", "high"]},
                },
            },
        },
        "ab_tests": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["hypothesis", "variant_a", "variant_b", "primary_metric", "guardrail_metric", "evidence_refs"],
                "properties": {
                    "hypothesis": {"type": "string"}, "variant_a": {"type": "string"}, "variant_b": {"type": "string"},
                    "primary_metric": {"type": "string"}, "guardrail_metric": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
}


DIMENSION_WEIGHTS = [
    ("hero", "Hero и первое впечатление", 15),
    ("gallery_funnel", "Сценарий галереи и воронка контента", 15),
    ("visual_quality", "Качество визуала и бренд-код", 10),
    ("mobile_readability", "Читаемость на мобильном", 5),
    ("value_pain", "Ценность, аудитория и закрытие боли", 10),
    ("benefit_proof", "Выгоды, механизм и доказательства", 10),
    ("structure_completeness", "Полнота текста и атрибутов", 10),
    ("objection_handling", "Снятие возражений и рисков", 10),
    ("trust_compliance", "Доверие и корректность обещаний", 5),
    ("conversion_readiness", "Готовность к конверсии", 10),
]


def _csv_models(env_key: str, defaults: list[str]) -> list[str]:
    raw = str(os.environ.get(env_key) or "").strip()
    values = [item.strip() for item in raw.split(",") if item.strip()] if raw else defaults
    return list(dict.fromkeys(values))


def resolve_codex_cli() -> str:
    candidates = [
        os.environ.get("CODEX_CLI_PATH"),
        str(Path.home() / ".codex" / ".sandbox-bin" / "codex-cli.exe"),
        str(Path.home() / ".codex" / ".sandbox-bin" / "codex.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return ""


def _probe_local_models() -> tuple[bool, list[str]]:
    base = str(os.environ.get("LM_STUDIO_BASE_URL") or "http://127.0.0.1:1234/v1").rstrip("/")
    endpoint = f"{base.rsplit('/chat/completions', 1)[0]}/models" if base.endswith("/chat/completions") else f"{base}/models"
    try:
        with urlopen(Request(endpoint, headers={"Accept": "application/json"}), timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = [str(item.get("id") or "").strip() for item in data.get("data") or [] if str(item.get("id") or "").strip()]
        return bool(models), models
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        fallback = str(os.environ.get("LM_STUDIO_MODEL") or "local-model")
        return False, [fallback]


def provider_catalog(credential: Callable[[str, str], str]) -> dict[str, Any]:
    local_available, local_models = _probe_local_models()
    codex_models = _csv_models("CONTENT_SCORING_CODEX_MODELS", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5"])
    openrouter_models = _csv_models("CONTENT_SCORING_OPENROUTER_MODELS", ["openai/gpt-5.5", "google/gemini-3-pro-preview"])
    cloud_models = _csv_models("CONTENT_SCORING_CLOUD_MODELS", ["gpt-5.5", "gpt-5.4"])
    codex_cli = resolve_codex_cli()
    openrouter_ready = bool(credential("openrouter", "api_key"))
    cloud_ready = bool(os.environ.get("CONTENT_SCORING_CLOUD_API_KEY") or credential("openai", "api_key"))
    providers = [
        {
            "id": "codex", "label": "Codex CLI", "available": bool(codex_cli), "models": codex_models,
            "default_model": codex_models[0],
            "reasoning_efforts": {model: _codex_reasoning_efforts(model) for model in codex_models},
            "default_reasoning_effort": "medium",
            "message": "" if codex_cli else "Codex CLI не найден в локальном runtime.",
        },
        {
            "id": "openrouter", "label": "OpenRouter", "available": openrouter_ready, "models": openrouter_models,
            "default_model": openrouter_models[0], "message": "" if openrouter_ready else "Подключите OpenRouter в Админка → Интеграции.",
        },
        {
            "id": "cloud", "label": "Cloud API", "available": cloud_ready, "models": cloud_models,
            "default_model": cloud_models[0], "message": "" if cloud_ready else "Cloud API не подключён.",
        },
        {
            "id": "local", "label": "Локальная модель", "available": local_available, "models": local_models,
            "default_model": local_models[0], "message": "" if local_available else "LM Studio не отвечает на локальном endpoint.",
        },
    ]
    return {"ok": True, "providers": providers, "default_provider": "codex", "schema_version": "1.1"}


def _codex_reasoning_efforts(model: str) -> list[str]:
    normalized = str(model or "").strip().lower()
    if normalized in {"gpt-5.6-sol", "gpt-5.6-terra"}:
        return ["low", "medium", "high", "xhigh", "max", "ultra"]
    if normalized in {"gpt-5.6-luna", "gpt-5.5", "gpt-5.4"}:
        return ["low", "medium", "high", "xhigh"] + (["max"] if normalized == "gpt-5.6-luna" else [])
    return ["low", "medium", "high"]


def scoring_input(card: dict[str, Any]) -> dict[str, Any]:
    attributes = []
    for row in card.get("attributes") or []:
        name = str(row.get("attribute_name") or "").strip()
        if name.casefold() in {"фото", "видео"}:
            continue
        value = row.get("value")
        text = "" if value is None else str(value)
        attributes.append({
            "name": name, "kind": str(row.get("attribute_kind") or ""), "filled": bool(row.get("filled")),
            "value": text[:1800], "state": str(row.get("state") or ""),
        })
    media = [
        {"ref": f"image:{index}", "label": str(item.get("label") or f"Фото {index}"), "url": str(item.get("url") or "")}
        for index, item in enumerate([item for item in card.get("media") or [] if item.get("kind") == "photo" and item.get("url")], start=1)
    ]
    return {
        "marketplace": card.get("marketplace"), "sku": card.get("sku"), "content_source": card.get("content_source"),
        "summary": card.get("summary") or {}, "attributes": attributes, "media": media,
        "observability_note": "Заказы и остаток — контекст периода, а не доказательство причинности контента. CTR, переходы, корзины и CVR отсутствуют, если явно не переданы.",
    }


def build_scoring_prompt(card: dict[str, Any]) -> str:
    weights = ", ".join(f"{key}={maximum}" for key, _label, maximum in DIMENSION_WEIGHTS)
    stages = "hero, problem, value proposition, benefits, mechanism, proof, objections, usage, specifications, trust, CTA"
    return (
        "Ты старший CRO-аналитик карточек Wildberries и Ozon. Проведи доказательный аудит эффективности карточки. "
        "Оцени не красоту сама по себе, а способность контента провести покупателя от первого экрана к уверенному заказу.\n\n"
        "ПРАВИЛА ДОКАЗАТЕЛЬНОСТИ:\n"
        "1. Используй только приложенные изображения и JSON карточки. Не выдумывай свойства, отзывы, конкурентов, нормы категории или влияние на продажи.\n"
        "2. Каждое существенное утверждение привяжи к image:N или field:<название>. Если элемент не читается, укажи not_observable.\n"
        "3. Разделяй наблюдение, интерпретацию и рекомендацию. Продажи/остаток — контекст, не причинное доказательство качества контента.\n"
        "4. Проверь каждое изображение отдельно: роль, этап воронки, композиция, свет/цвет, масштаб товара, читаемость текста на мобильном, конкретность выгоды, доказательность, перегруз, повторяемость и следующий шаг.\n"
        f"5. Проверь покрытие воронки контента: {stages}.\n"
        "6. Hero должен за 1–2 секунды объяснять продукт/вариант и визуально выделять товар без недоказанных обещаний. Выгода должна отвечать на боль через механизм и proof, а не через общий рекламный эпитет.\n"
        "7. Рекомендации формулируй как конкретную правку кадра/копирайта/порядка с ожидаемым направлением метрики и проверяемым A/B тестом. Не обещай численный uplift без эксперимента.\n\n"
        f"ВЕСА, сумма 100: {weights}. Итоговый overall_score обязан равняться сумме score по dimensions; max_score — соответствующему весу.\n"
        "Верни строго JSON по заданной схеме. Evidence id используй как E1, E2...; ссылки в остальных блоках должны указывать на эти id и/или прямые source_ref.\n\n"
        "СТРУКТУРА КАРТОЧКИ:\n" + json.dumps(scoring_input(card), ensure_ascii=False, indent=2, default=str)
    )


def _safe_image_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = str(parsed.hostname or "").lower()
    allowed = ("wbbasket.ru", "wb.ru", "ozonusercontent.com", "ozon.ru", "ozone.ru")
    return parsed.scheme == "https" and bool(host) and any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed)


def _download_images(card: dict[str, Any], directory: Path, limit: int = 24) -> list[Path]:
    paths = []
    photos = [item for item in card.get("media") or [] if item.get("kind") == "photo" and (_safe_image_url(item.get("url")) or _safe_image_url(item.get("thumbnail_url")))][:limit]
    for index, item in enumerate(photos, start=1):
        data, content_type = b"", ""
        urls = list(dict.fromkeys(str(value) for value in (item.get("url"), item.get("thumbnail_url")) if _safe_image_url(value)))
        for url in urls:
            request = Request(url, headers={"User-Agent": "PULSE-Content-Scoring/1.0"})
            try:
                with urlopen(request, timeout=35) as response:
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    data = response.read(8 * 1024 * 1024 + 1)
                if content_type.startswith("image/") and len(data) <= 8 * 1024 * 1024:
                    break
            except (HTTPError, URLError, TimeoutError, OSError):
                data, content_type = b"", ""
        if not data:
            continue
        if len(data) > 8 * 1024 * 1024 or not content_type.startswith("image/"):
            continue
        suffix = ".png" if "png" in content_type else ".webp" if "webp" in content_type else ".jpg"
        path = directory / f"image-{index:02d}{suffix}"
        path.write_bytes(data)
        paths.append(path)
    return paths


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ContentScoringError("Модель не вернула JSON-результат скоринга.")
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ContentScoringError("JSON модели не прошёл разбор.") from exc
    if not isinstance(value, dict):
        raise ContentScoringError("Модель вернула результат неверного типа.")
    return value


def _validate_result(result: dict[str, Any]) -> dict[str, Any]:
    dimensions = result.get("dimensions") or []
    total = round(sum(float(item.get("score") or 0) for item in dimensions), 2)
    overall = float(result.get("overall_score") or 0)
    if abs(total - overall) > 0.51:
        result["overall_score"] = int(round(total))
        result.setdefault("data_gaps", []).append("Итог модели нормализован как сумма баллов по критериям.")
    result["overall_score"] = max(0, min(100, int(round(float(result.get("overall_score") or 0)))))
    result["confidence_pct"] = max(0, min(100, int(round(float(result.get("confidence_pct") or 0)))))
    return result


def _run_codex(card: dict[str, Any], model: str, reasoning_effort: str) -> dict[str, Any]:
    cli = resolve_codex_cli()
    if not cli:
        raise ContentScoringError("Codex CLI не найден в локальном runtime.")
    with tempfile.TemporaryDirectory(prefix="pulse-content-score-") as tmp:
        root = Path(tmp)
        images = _download_images(card, root)
        if not images:
            raise ContentScoringError("Не удалось подготовить изображения карточки для Codex.")
        schema_path, output_path = root / "schema.json", root / "result.json"
        schema_path.write_text(json.dumps(SCORING_SCHEMA, ensure_ascii=False), encoding="utf-8")
        command = [
            cli, "exec", "--ephemeral", "--ignore-rules", "--skip-git-repo-check", "-C", str(root),
            "-s", "read-only", "-m", model, "-c", f'model_reasoning_effort="{reasoning_effort}"',
            "--output-schema", str(schema_path),
            "--output-last-message", str(output_path), "--color", "never",
        ]
        for path in images:
            command.extend(["-i", str(path)])
        command.append("-")
        try:
            completed = subprocess.run(
                command, input=build_scoring_prompt(card), text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=int(os.environ.get("CONTENT_SCORING_CODEX_TIMEOUT_SECONDS", "900")),
                cwd=str(root), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContentScoringError("Codex не завершил скоринг за отведённое время.") from exc
        if completed.returncode != 0 or not output_path.is_file():
            raise ContentScoringError("Codex CLI не сформировал результат скоринга.")
        result = _extract_json(output_path.read_text(encoding="utf-8"))
        result["__attached_image_count"] = len(images)
        return result


def _vision_request(endpoint: str, api_key: str, model: str, card: dict[str, Any], timeout: int) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": build_scoring_prompt(card)}]
    for item in [item for item in card.get("media") or [] if item.get("kind") == "photo" and (_safe_image_url(item.get("url")) or _safe_image_url(item.get("thumbnail_url")))][:24]:
        image_url = item.get("url") if _safe_image_url(item.get("url")) else item.get("thumbnail_url")
        content.append({"type": "image_url", "image_url": {"url": str(image_url)}})
    body = {
        "model": model, "temperature": 0.1,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "marketplace_content_scoring", "strict": True, "schema": SCORING_SCHEMA}},
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ContentScoringError(f"Провайдер вернул HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ContentScoringError("Провайдер модели недоступен или вернул некорректный ответ.") from exc
    message = (payload.get("choices") or [{}])[0].get("message") or {}
    return _extract_json(message.get("content") or "")


def run_scoring(
    card: dict[str, Any], provider: str, model: str, credential: Callable[[str, str], str],
    reasoning_effort: str = "",
) -> dict[str, Any]:
    catalog = provider_catalog(credential)
    selected = next((item for item in catalog["providers"] if item["id"] == provider), None)
    if not selected:
        raise ContentScoringError("Неизвестный провайдер модели.")
    if not selected["available"]:
        raise ContentScoringError(selected["message"] or "Провайдер не подключён.")
    if model not in selected["models"]:
        raise ContentScoringError("Выбранная модель отсутствует в списке провайдера.")
    if provider == "codex":
        supported_efforts = list((selected.get("reasoning_efforts") or {}).get(model) or _codex_reasoning_efforts(model))
        reasoning_effort = str(reasoning_effort or selected.get("default_reasoning_effort") or "medium").strip().lower()
        if reasoning_effort not in supported_efforts:
            raise ContentScoringError("Выбранное усилие рассуждения не поддерживается этой моделью.")
        result = _run_codex(card, model, reasoning_effort)
    elif provider == "openrouter":
        result = _vision_request("https://openrouter.ai/api/v1/chat/completions", credential("openrouter", "api_key"), model, card, 600)
    elif provider == "cloud":
        endpoint = str(os.environ.get("CONTENT_SCORING_CLOUD_CHAT_URL") or "https://api.openai.com/v1/chat/completions")
        key = str(os.environ.get("CONTENT_SCORING_CLOUD_API_KEY") or credential("openai", "api_key"))
        result = _vision_request(endpoint, key, model, card, 600)
    else:
        base = str(os.environ.get("LM_STUDIO_BASE_URL") or "http://127.0.0.1:1234/v1").rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        result = _vision_request(endpoint, "", model, card, 600)
    attached_image_count = int(result.pop("__attached_image_count", 0) or 0)
    source_image_count = len([item for item in card.get("media") or [] if item.get("kind") == "photo"])
    if provider != "codex":
        attached_image_count = len([
            item for item in card.get("media") or []
            if item.get("kind") == "photo" and (_safe_image_url(item.get("url")) or _safe_image_url(item.get("thumbnail_url")))
        ][:24])
    result = _validate_result(result)
    if attached_image_count < source_image_count:
        result.setdefault("data_gaps", []).append(
            f"Источник объявил {source_image_count} фото, модели удалось передать {attached_image_count}; "
            "недоступные кадры не оценивались."
        )
    result["run"] = {
        "provider": provider, "provider_label": selected["label"], "model": model,
        "reasoning_effort": reasoning_effort if provider == "codex" else None,
        "generated_at": datetime.now(timezone.utc).isoformat(), "schema_version": "1.1",
        "input_fingerprint": hashlib.sha256(json.dumps(scoring_input(card), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16],
        "source_image_count": source_image_count,
        "attached_image_count": attached_image_count,
        "image_count": attached_image_count,
    }
    return {"ok": True, "result": result}
