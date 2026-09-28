"""Semantic, versioned classification of WB search queries for supported clients."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app


CLASSIFIER_VERSION = "gj-wb-semantic-v1.1"
CLIENT_CLASSIFIER_VERSIONS = {
    "gloria_jeans": CLASSIFIER_VERSION,
    "sportmaster": "sportmaster-wb-semantic-v1.0",
}
SUPPORTED_CLIENTS = tuple(CLIENT_CLASSIFIER_VERSIONS)
BATCH_SIZE = 5_000

BRAND_LABELS = {
    "gloria_jeans": "Брендовый: Gloria Jeans",
    "competitor": "Брендовый: другой бренд",
    "generic": "Без бренда",
}
QUERY_TYPE_LABELS = {
    "brand": "Брендовый",
    "brand_category": "Бренд + категория",
    "category": "Категорийный",
    "category_attribute": "Категория + уточнение",
    "sku_article": "Артикул / SKU",
    "other": "Прочее",
}
AUDIENCE_LABELS = {
    "women": "Женщины",
    "men": "Мужчины",
    "girls": "Девочки",
    "boys": "Мальчики",
    "children": "Дети",
    "baby": "Малыши",
    "teen": "Подростки",
    "unisex": "Унисекс",
    "multiple": "Несколько аудиторий",
    "unspecified": "Не указана",
}
SPECIFICITY_LABELS = {
    "head": "Короткий (1–2 слова)",
    "middle": "Средний (3–4 слова)",
    "long_tail": "Длинный хвост (5+ слов)",
}
SCENARIO_LABELS = {
    "school": "Школа",
    "office": "Офис",
    "sport": "Спорт",
    "home_sleep": "Дом / сон",
    "beach_swim": "Пляж / плавание",
    "festive": "Праздник / выход",
    "summer": "Лето",
    "winter": "Зима",
    "maternity": "Беременность / кормление",
    "running": "Бег",
    "football": "Футбол",
    "fitness": "Фитнес / тренировки",
    "tourism": "Туризм / кемпинг",
    "cycling": "Велоспорт",
    "winter_sport": "Зимний спорт",
}

GLORIA_EXACT_TOKENS = {
    "gloria", "глория", "глории", "глори", "глориа", "gloriya", "glorya",
    "gloriajeans", "глорияджинс", "gj",
}
GLORIA_COMPACT_PHRASES = {
    "gloriajeans", "глорияджинс", "глорияджинсодежда", "gloriajeansclothes",
    "ukjhbzlbyc",
}
GLORIA_FUZZY_TARGETS = ("gloria", "глория")

SPORTMASTER_EXACT_TOKENS = {
    "sportmaster", "спортмастер", "спортмастера", "спортмастеру",
}
SPORTMASTER_COMPACT_PHRASES = {"sportmaster", "спортмастер"}
SPORTMASTER_FUZZY_TARGETS = ("sportmaster", "спортмастер")

COMPETITOR_PATTERNS = (
    ("Befree", ("befree", "бифри")),
    ("Sela", ("sela", "села")),
    ("Zarina", ("zarina", "зарина")),
    ("ТВОЕ", ("твое", "tvoe")),
    ("O'stin", ("ostin", "o stin", "остин")),
    ("Zara", ("zara", "зара")),
    ("Mango", ("mango", "манго")),
    ("Koton", ("koton", "котон")),
    ("Colin's", ("colins", "colin s", "колинс")),
    ("Lime", ("lime", "лайм")),
    ("Sinsay", ("sinsay", "синсей")),
    ("Reserved", ("reserved", "резервед")),
    ("Modis", ("modis", "модис")),
    ("Uniqlo", ("uniqlo", "юникло")),
    ("H&M", ("h m", "hm", "эйч энд эм")),
    ("Love Republic", ("love republic", "лав репаблик")),
    ("oodji", ("oodji", "оджи")),
    ("Concept Club", ("concept club", "концепт клаб")),
    ("Baon", ("baon", "баон")),
    ("Finn Flare", ("finn flare", "фин флаер")),
    ("LC Waikiki", ("lc waikiki", "вайкики")),
    ("Acoola", ("acoola", "акула")),
)

CATEGORY_STEMS = (
    "плать", "футбол", "джинс", "брюк", "шорт", "купаль", "юбк", "рубаш",
    "блуз", "носк", "трус", "пижам", "майк", "топ", "куртк", "толстов",
    "худи", "свитер", "свитшот", "джемпер", "кардиган", "жакет", "пиджак",
    "костюм", "комбинез", "сарафан", "легин", "лосин", "плавк", "бюст",
    "боди", "бель", "колгот", "кепк", "шапк", "панам", "сумк", "рюкзак",
    "ремень", "кроссов", "кед", "ботин", "сандал", "тапоч", "пальто",
    "плащ", "ветров", "жилет", "парк", "бомбер", "лонгслив", "водолаз",
    "джоггер", "кюлот", "гольф", "вареж", "перчат", "шарф", "галстук",
    "закол", "ободок", "резинк", "очк", "кошелек", "портмон", "поло",
    "туник", "болеро", "шортик", "боксеры", "бюстгальтер", "пеньюар",
    "велосипед", "самокат", "мяч", "ракетк", "тренаж", "гантел",
    "гирь", "лыж", "сноуборд", "коньк", "ролик", "скейт", "палат",
    "спальник", "термос", "бутыл", "бутс", "щитк", "шлем", "удоч",
    "лодк", "сапог", "клюшк", "маск", "ласты", "коврик", "скакалк",
)
CATEGORY_WORDS = {
    "dress", "dresses", "tshirt", "shirt", "jeans", "shorts", "skirt",
    "swimsuit", "hoodie", "jacket", "pants", "trousers", "socks", "underwear",
    "pajama", "pyjama", "top", "coat", "cardigan", "sweater", "bag", "backpack",
}

ATTRIBUTE_GROUPS = {
    "color": (
        "бел", "черн", "красн", "син", "голуб", "зелен", "розов", "бежев",
        "сер", "коричнев", "желт", "фиолет", "оранжев", "хаки", "бордов",
        "молочн", "разноцвет", "white", "black", "red", "blue", "green", "pink",
    ),
    "material": (
        "хлоп", "деним", "джинсов", "льня", "лен", "кож", "замш", "вязан",
        "трикотаж", "шерст", "кашемир", "флис", "полиэстер", "вискоз", "атлас",
        "шелк", "муслин", "хлопков", "cotton", "denim", "leather", "linen",
    ),
    "fit_style": (
        "оверсайз", "oversize", "свободн", "облега", "притал", "широк",
        "узк", "прям", "расклеш", "клеш", "высокая посад", "низкая посад",
        "укороч", "удлин", "длинн", "коротк", "макси", "миди", "мини",
        "с принтом", "однотон", "базов", "классичес", "модн", "нарядн",
    ),
    "size": (
        "большого размера", "plus size", "оверсайз", "рост ", "размер ",
        "для полных", "высоких", "низких",
    ),
}

AUDIENCE_PATTERNS = {
    "girls": ("для девоч", "девочк", "девочки"),
    "boys": ("для мальчик", "мальчик", "мальчики"),
    "women": ("женск", "женщин", "девуш"),
    "men": ("мужск", "мужчин", "парн"),
    "baby": ("новорож", "малыш", "груднич", "беби", "baby"),
    "teen": ("подрост", "тинейдж", "teen"),
    "children": ("детск", "для детей", "ребен"),
    "unisex": ("унисекс", "unisex"),
}

SCENARIO_PATTERNS = {
    "school": ("школ", "школьн", "1 сентября", "выпускн"),
    "office": ("офис", "делов", "работ"),
    "sport": ("спорт", "фитнес", "трениров", "бег", "йог"),
    "home_sleep": ("домаш", "для дома", "для сна", "пижам", "ночн"),
    "beach_swim": ("пляж", "море", "купаль", "плавк", "бассейн"),
    "festive": ("празд", "вечерн", "нарядн", "на выход", "свадьб"),
    "summer": ("летн", "лето"),
    "winter": ("зимн", "зима", "тепл", "утеплен"),
    "maternity": ("беременн", "кормящ", "для кормления"),
    "running": ("бег", "бегов", "running", "марафон"),
    "football": ("футбол", "футз", "бутс"),
    "fitness": ("фитнес", "трениров", "кроссфит", "тренаж"),
    "tourism": ("туризм", "поход", "кемпинг", "палат", "треккинг"),
    "cycling": ("велосипед", "велоспорт", "велош"),
    "winter_sport": ("лыж", "сноуборд", "коньк"),
}

ARTICLE_TOKEN_RE = re.compile(r"(?<!\w)(?:\d{6,}|(?=[a-zа-я]*\d)(?=\d*[a-zа-я])[a-zа-я0-9-]{5,})(?!\w)")
WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


@dataclass(frozen=True)
class QueryClassification:
    search_query: str
    normalized_query: str
    brand_class: str
    brand_name: str | None
    query_type: str
    is_gloria_brand: bool
    is_competitor_brand: bool
    is_category: bool
    is_attribute: bool
    is_audience: bool
    is_scenario: bool
    is_sku_article: bool
    audience: str
    specificity: str
    scenario_tags: tuple[str, ...]
    attribute_tags: tuple[str, ...]
    matched_terms: tuple[str, ...]
    confidence: float

    def database_row(self, classifier_version: str = CLASSIFIER_VERSION) -> tuple[Any, ...]:
        return (
            self.search_query, self.normalized_query, self.brand_class, self.brand_name,
            self.query_type, self.is_gloria_brand, self.is_competitor_brand,
            self.is_category, self.is_attribute, self.is_audience, self.is_scenario,
            self.is_sku_article, self.audience, self.specificity,
            list(self.scenario_tags), list(self.attribute_tags), list(self.matched_terms),
            self.confidence, classifier_version,
        )


def normalize_query(value: str) -> str:
    return " ".join(WORD_RE.findall((value or "").lower().replace("ё", "е")))


def bounded_levenshtein(left: str, right: str, limit: int = 2) -> int:
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_min = left_index
        for right_index, right_char in enumerate(right, start=1):
            value = min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def gloria_match(tokens: list[str], compact: str) -> tuple[bool, list[str], float]:
    matched: list[str] = []
    if any(phrase in compact for phrase in GLORIA_COMPACT_PHRASES):
        matched.extend(phrase for phrase in GLORIA_COMPACT_PHRASES if phrase in compact)
        return True, matched, 0.99
    for token in tokens:
        if token in GLORIA_EXACT_TOKENS:
            matched.append(token)
    if matched:
        confidence = 0.98 if any(token not in {"gj"} for token in matched) else 0.92
        return True, sorted(set(matched)), confidence
    for token in tokens:
        if not 4 <= len(token) <= 8:
            continue
        for target in GLORIA_FUZZY_TARGETS:
            if token[0] == target[0] and bounded_levenshtein(token, target) <= 2:
                return True, [token], 0.84
    return False, [], 0.0


def client_brand_match(client_key: str, tokens: list[str], compact: str) -> tuple[bool, list[str], float]:
    if client_key != "sportmaster":
        return gloria_match(tokens, compact)
    matched = sorted({token for token in tokens if token in SPORTMASTER_EXACT_TOKENS})
    compact_matches = sorted(phrase for phrase in SPORTMASTER_COMPACT_PHRASES if phrase in compact)
    if compact_matches:
        return True, compact_matches, 0.99
    if matched:
        return True, matched, 0.98
    for token in tokens:
        if len(token) < 8:
            continue
        for target in SPORTMASTER_FUZZY_TARGETS:
            if token[0] == target[0] and bounded_levenshtein(token, target) <= 2:
                return True, [token], 0.84
    return False, [], 0.0


def build_brand_catalog(values: Iterable[str]) -> tuple[tuple[str, str], ...]:
    catalog: dict[str, str] = {}
    excluded = {"без бренда", "no name", "noname", "нет бренда"}
    for value in values:
        brand_name = (value or "").strip()
        normalized = normalize_query(brand_name)
        if normalized in excluded or len(normalized.replace(" ", "")) < 3:
            continue
        catalog.setdefault(normalized, brand_name)
    return tuple(sorted(catalog.items(), key=lambda item: (-len(item[0]), item[0])))


def catalog_brand_match(
    normalized: str,
    tokens: list[str],
    brand_catalog: tuple[tuple[str, str], ...],
) -> tuple[str | None, list[str]]:
    for term, brand_name in brand_catalog:
        if contains_term(normalized, tokens, term):
            return brand_name, [term]
    return None, []


def contains_term(normalized: str, tokens: list[str], term: str) -> bool:
    if " " in term:
        return term in normalized
    return term in tokens


def competitor_match(normalized: str, tokens: list[str]) -> tuple[str | None, list[str]]:
    for brand_name, terms in COMPETITOR_PATTERNS:
        matched = [term for term in terms if contains_term(normalized, tokens, term)]
        if matched:
            return brand_name, matched
    return None, []


def classify_query(
    search_query: str,
    client_key: str = "gloria_jeans",
    brand_catalog: tuple[tuple[str, str], ...] = (),
) -> QueryClassification:
    normalized = normalize_query(search_query)
    tokens = normalized.split()
    compact = "".join(tokens)
    is_client_brand, client_terms, client_confidence = client_brand_match(client_key, tokens, compact)
    competitor_name, competitor_terms = competitor_match(normalized, tokens)
    if not competitor_name:
        competitor_name, competitor_terms = catalog_brand_match(normalized, tokens, brand_catalog)
    if is_client_brand:
        brand_class = client_key
        brand_name = "Gloria Jeans" if client_key == "gloria_jeans" else "Спортмастер"
        matched_terms = client_terms
        confidence = client_confidence
    elif competitor_name:
        brand_class, brand_name = "competitor", competitor_name
        matched_terms = competitor_terms
        confidence = 0.94 if competitor_name != "ТВОЕ" else 0.82
    else:
        brand_class, brand_name = "generic", None
        matched_terms = []
        confidence = 0.96

    category_terms = sorted({
        token
        for token in tokens
        if token in CATEGORY_WORDS or any(token.startswith(stem) for stem in CATEGORY_STEMS)
    })
    if client_key == "gloria_jeans" and is_client_brand and ("gloriajeans" in compact or "глорияджинс" in compact):
        category_terms = [
            token
            for token in category_terms
            if token not in {"jeans", "джинс"}
        ]
    attribute_tags = sorted({
        group
        for group, terms in ATTRIBUTE_GROUPS.items()
        if any(term in normalized for term in terms)
    })
    audiences = [
        audience
        for audience, terms in AUDIENCE_PATTERNS.items()
        if any(term in normalized for term in terms)
    ]
    specific_audiences = [value for value in audiences if value not in {"children", "teen"}]
    if len(set(specific_audiences)) > 1:
        audience = "multiple"
    elif specific_audiences:
        audience = specific_audiences[0]
    elif audiences:
        audience = audiences[0]
    else:
        audience = "unspecified"
    scenario_tags = tuple(sorted(
        scenario
        for scenario, terms in SCENARIO_PATTERNS.items()
        if any(term in normalized for term in terms)
    ))
    is_sku_article = bool(ARTICLE_TOKEN_RE.search(normalized))
    is_category = bool(category_terms)
    is_attribute = bool(attribute_tags)
    is_audience = audience != "unspecified"
    is_scenario = bool(scenario_tags)

    if is_sku_article and not is_category and brand_class == "generic":
        query_type = "sku_article"
    elif brand_class != "generic" and is_category:
        query_type = "brand_category"
    elif brand_class != "generic":
        query_type = "brand"
    elif is_category and (is_attribute or is_audience or is_scenario or len(tokens) >= 3):
        query_type = "category_attribute"
    elif is_category:
        query_type = "category"
    else:
        query_type = "other"

    word_count = len(tokens)
    specificity = "head" if word_count <= 2 else "middle" if word_count <= 4 else "long_tail"
    all_matched = tuple(sorted(set(matched_terms + category_terms)))
    return QueryClassification(
        search_query=search_query,
        normalized_query=normalized,
        brand_class=brand_class,
        brand_name=brand_name,
        query_type=query_type,
        is_gloria_brand=is_client_brand and client_key == "gloria_jeans",
        is_competitor_brand=brand_class == "competitor",
        is_category=is_category,
        is_attribute=is_attribute,
        is_audience=is_audience,
        is_scenario=is_scenario,
        is_sku_article=is_sku_article,
        audience=audience,
        specificity=specificity,
        scenario_tags=scenario_tags,
        attribute_tags=tuple(attribute_tags),
        matched_terms=all_matched,
        confidence=confidence,
    )


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.wb_search_query_classification (
                search_query text PRIMARY KEY,
                normalized_query text NOT NULL,
                brand_class text NOT NULL,
                brand_name text,
                query_type text NOT NULL,
                is_gloria_brand boolean NOT NULL,
                is_competitor_brand boolean NOT NULL,
                is_category boolean NOT NULL,
                is_attribute boolean NOT NULL,
                is_audience boolean NOT NULL,
                is_scenario boolean NOT NULL,
                is_sku_article boolean NOT NULL,
                audience text NOT NULL,
                specificity text NOT NULL,
                scenario_tags text[] NOT NULL DEFAULT '{}',
                attribute_tags text[] NOT NULL DEFAULT '{}',
                matched_terms text[] NOT NULL DEFAULT '{}',
                confidence numeric(4,3) NOT NULL,
                classifier_version text NOT NULL,
                classified_at timestamp with time zone NOT NULL DEFAULT now()
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_wb_query_class_brand ON public.wb_search_query_classification(brand_class)",
            "CREATE INDEX IF NOT EXISTS idx_wb_query_class_type ON public.wb_search_query_classification(query_type)",
            "CREATE INDEX IF NOT EXISTS idx_wb_query_class_audience ON public.wb_search_query_classification(audience)",
            "CREATE INDEX IF NOT EXISTS idx_wb_query_class_specificity ON public.wb_search_query_classification(specificity)",
            "CREATE INDEX IF NOT EXISTS idx_wb_query_class_scenarios ON public.wb_search_query_classification USING gin(scenario_tags)",
        ):
            cur.execute(statement)


UPSERT_SQL = """
    INSERT INTO public.wb_search_query_classification (
        search_query, normalized_query, brand_class, brand_name, query_type,
        is_gloria_brand, is_competitor_brand, is_category, is_attribute,
        is_audience, is_scenario, is_sku_article, audience, specificity,
        scenario_tags, attribute_tags, matched_terms, confidence,
        classifier_version
    ) VALUES %s
    ON CONFLICT (search_query) DO UPDATE SET
        normalized_query = EXCLUDED.normalized_query,
        brand_class = EXCLUDED.brand_class,
        brand_name = EXCLUDED.brand_name,
        query_type = EXCLUDED.query_type,
        is_gloria_brand = EXCLUDED.is_gloria_brand,
        is_competitor_brand = EXCLUDED.is_competitor_brand,
        is_category = EXCLUDED.is_category,
        is_attribute = EXCLUDED.is_attribute,
        is_audience = EXCLUDED.is_audience,
        is_scenario = EXCLUDED.is_scenario,
        is_sku_article = EXCLUDED.is_sku_article,
        audience = EXCLUDED.audience,
        specificity = EXCLUDED.specificity,
        scenario_tags = EXCLUDED.scenario_tags,
        attribute_tags = EXCLUDED.attribute_tags,
        matched_terms = EXCLUDED.matched_terms,
        confidence = EXCLUDED.confidence,
        classifier_version = EXCLUDED.classifier_version,
        classified_at = now()
"""


def rebuild_summary_view(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_wb_search_query_classification_summary")
        cur.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_search_query_classification_summary AS
            WITH demand AS (
                SELECT search_query, sum(query_count)::bigint AS search_demand
                FROM public.mv_wb_search_query_daily
                GROUP BY search_query
            )
            SELECT
                c.brand_class,
                c.brand_name,
                c.query_type,
                c.audience,
                c.specificity,
                count(*)::bigint AS search_query_count,
                coalesce(sum(d.search_demand), 0)::bigint AS search_demand
            FROM public.wb_search_query_classification c
            JOIN demand d USING (search_query)
            GROUP BY c.brand_class, c.brand_name, c.query_type, c.audience, c.specificity
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX ux_mv_wb_query_class_summary
            ON public.mv_wb_search_query_classification_summary(
                brand_class, coalesce(brand_name, ''), query_type, audience, specificity
            )
            """
        )
        cur.execute("ANALYZE public.wb_search_query_classification")
        cur.execute("ANALYZE public.mv_wb_search_query_classification_summary")


def batches(rows: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}ч {minutes:02d}м {secs:02d}с"
    if minutes:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


def classify_database(
    conn: Any,
    batch_size: int = BATCH_SIZE,
    client_key: str = "gloria_jeans",
) -> dict[str, Any]:
    started_at = time.monotonic()
    ensure_schema(conn)
    if client_key not in SUPPORTED_CLIENTS:
        raise ValueError(f"Неподдерживаемый клиент классификатора: {client_key}")
    classifier_version = CLIENT_CLASSIFIER_VERSIONS[client_key]
    client_label = "Gloria Jeans" if client_key == "gloria_jeans" else "Спортмастер"
    with conn.cursor() as cur:
        cur.execute("SELECT count(DISTINCT search_query) AS total FROM public.mv_wb_search_query_daily")
        total = int(cur.fetchone()["total"])
        cur.execute("SELECT DISTINCT brand FROM public.wb_search_queries_daily WHERE nullif(trim(brand), '') IS NOT NULL")
        brand_catalog = build_brand_catalog(row["brand"] for row in cur.fetchall())
    print(
        "ПЛАН: "
        f"уникальных запросов {total:,} | пакет {batch_size:,} | "
        f"клиент {client_label} | брендов в словаре {len(brand_catalog):,} | "
        f"версия {classifier_version} | пауз и API-лимитов нет",
        flush=True,
    )

    read_cur = conn.cursor(name="wb_query_classification_source", cursor_factory=RealDictCursor)
    read_cur.itersize = batch_size
    read_cur.execute("SELECT DISTINCT search_query FROM public.mv_wb_search_query_daily ORDER BY search_query")
    write_cur = conn.cursor()
    processed = 0
    counters: Counter[str] = Counter()
    while True:
        source_rows = read_cur.fetchmany(batch_size)
        if not source_rows:
            break
        classifications = [
            classify_query(row["search_query"], client_key, brand_catalog)
            for row in source_rows
        ]
        execute_values(write_cur, UPSERT_SQL, [item.database_row(classifier_version) for item in classifications], page_size=batch_size)
        processed += len(classifications)
        counters.update(item.brand_class for item in classifications)
        elapsed = time.monotonic() - started_at
        eta = elapsed / processed * (total - processed) if processed else 0
        print(
            f"ПРОГРЕСС: {processed:,}/{total:,} ({processed / max(total, 1) * 100:.1f}%) | "
            f"{client_label} {counters[client_key]:,} | другие бренды {counters['competitor']:,} | "
            f"без бренда {counters['generic']:,} | прошло {format_duration(elapsed)} | "
            f"ETA {format_duration(eta)}",
            flush=True,
        )
    read_cur.close()
    write_cur.execute(
        """
        DELETE FROM public.wb_search_query_classification c
        WHERE NOT EXISTS (
            SELECT 1 FROM public.mv_wb_search_query_daily q WHERE q.search_query = c.search_query
        )
        """
    )
    removed = write_cur.rowcount
    write_cur.close()
    rebuild_summary_view(conn)
    elapsed = time.monotonic() - started_at
    result = {
        "processed": processed,
        "client_key": client_key,
        "client_brand": counters[client_key],
        "competitor": counters["competitor"],
        "generic": counters["generic"],
        "removed": removed,
        "elapsed_seconds": round(elapsed, 1),
        "classifier_version": classifier_version,
    }
    if client_key == "gloria_jeans":
        result["gloria_jeans"] = counters["gloria_jeans"]
    print(
        "ИТОГ: "
        f"размечено {processed:,}; {client_label} {counters[client_key]:,}; "
        f"другие бренды {counters['competitor']:,}; без бренда {counters['generic']:,}; "
        f"удалено устаревших {removed:,}; ошибок 0; "
        "таблица public.wb_search_query_classification; "
        "матвью public.mv_wb_search_query_classification_summary; "
        f"время {format_duration(elapsed)}",
        flush=True,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Классификация поисковых запросов WB")
    parser.add_argument("--client", choices=SUPPORTED_CLIENTS, default="gloria_jeans")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    conn = psycopg2.connect(**app.read_db_config(args.client), cursor_factory=RealDictCursor)
    try:
        classify_database(conn, max(500, args.batch_size), client_key=args.client)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
