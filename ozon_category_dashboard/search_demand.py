"""Source-separated search-frequency baskets; never marketplace order volumes."""
import math
import statistics
from collections import defaultdict
from datetime import date


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS public.pulse_search_demand_baskets (
            source text NOT NULL, category text NOT NULL, specification jsonb NOT NULL,
            collected_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(source,category));
            CREATE TABLE IF NOT EXISTS public.pulse_search_demand_observations (
            source text NOT NULL, query text NOT NULL, observation_date date NOT NULL,
            frequency double precision NOT NULL CHECK(frequency >= 0),
            source_url text NOT NULL, collected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(source,query,observation_date));""")


def seasonal_profile(observations, as_of, monthly_source=False):
    """Mean reported frequency snapshots per month; two complete past years required.

    Frequencies may be overlapping rolling windows, so they are never summed as
    monthly search totals. Each year's 12-month mean removes that year's level.
    """
    buckets = defaultdict(dict)
    for row in observations:
        day = date.fromisoformat(str(row['observation_date']))
        value = float(row['frequency'])
        if day < as_of.replace(day=1) and math.isfinite(value) and value >= 0:
            buckets[(day.year, day.month)][day] = value
    monthly = {key: statistics.mean(values.values()) for key, values in buckets.items() if len(values) >= (1 if monthly_source else 3)}
    years = [year for year in sorted({y for y, _ in monthly}) if year < as_of.year
             and all((year, m) in monthly for m in range(1, 13))]
    indices, used_years = [], []
    for year in years[-3:]:
        values = [monthly[year, month] for month in range(1, 13)]
        mean = statistics.mean(values)
        if mean > 0:
            indices.append([v / mean for v in values])
            used_years.append(year)
    ready = len(indices) >= 2
    result = [statistics.median(x[m] for x in indices) for m in range(12)] if ready else None
    if result:
        mean = statistics.mean(result)
        result = [value / mean for value in result]
    return {'indices': result, 'years': used_years, 'observed_months': len(monthly),
            'history': [{'month': f'{year:04d}-{month:02d}-01', 'frequency_mean': value,
                         'observations': len(buckets[year, month])} for (year, month), value in sorted(monthly.items())]}


def build_payload(baskets, observations, categories, months, as_of):
    by_query = defaultdict(list)
    for row in observations:
        by_query[row['source'], row['query']].append(row)
    profiles = {key: seasonal_profile(rows, as_of, monthly_source=key[0]=='wordstat') for key, rows in by_query.items()}
    result = []
    for category in categories:
        sources = {}
        for source in ['mpstats_wb', 'wordstat']:
            basket = next((r for r in baskets if r['source'] == source and r['category'] == category), None)
            spec = basket['specification'] if basket else {}
            queries = []
            for entry in spec.get('queries', []):
                profile = profiles.get((source, entry['query']), {'indices': None, 'years': [], 'observed_months': 0, 'history': []})
                queries.append({**entry, **profile})
            ready = bool(queries) and all(q['indices'] is not None for q in queries) and spec.get('mapping_status') == 'accepted'
            weights = [float(q.get('weight', 0)) for q in queries]
            total = sum(weights)
            index = [sum(q['indices'][m] * w for q, w in zip(queries, weights)) / total for m in range(12)] if ready and total > 0 else None
            month_rows = []
            for month in months:
                m = int(month[5:7]) - 1
                month_rows.append({'month_start': month, 'index': index[m] if index else None,
                                   'coefficient': index[m] / index[(m-1) % 12] if index and index[(m-1) % 12] > 0 else None})
            sources[source] = {'status': 'ready' if index else 'partial' if queries else 'unavailable',
                               'reason': spec.get('reason') or ('Нет полного годового покрытия по запросам' if queries and not index else 'Источник ещё не загружен' if not queries else ''),
                               'queries': queries, 'months': month_rows, 'collected_at': basket.get('collected_at') if basket else None,
                               'mapping_status': spec.get('mapping_status'), 'market_path': spec.get('market_path'),
                               'candidate_count': spec.get('candidate_count'), 'ranking_period': spec.get('ranking_period'),
                               'selection_note': spec.get('selection_note')}
        result.append({'category': category, 'sources': sources})
    return {'categories': result, 'months': months, 'application': 'comparison_only',
            'methodology': 'Индекс месяца — средняя частотность наблюдений месяца / средняя частотность года; медиана за 2–3 полных года. Корзина взвешена по частотности выбранных запросов. К спроса = индекс месяца / индекс предыдущего. Частотности источников не суммируются. Поисковый спрос заменяет сезонность, если вы явно примените его к сценарию.'}


def read_sources(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.pulse_search_demand_baskets') AS name")
        if not cur.fetchone()['name']:
            return [], []
        cur.execute('SELECT source,category,specification,collected_at::text FROM public.pulse_search_demand_baskets')
        baskets = [dict(r) for r in cur.fetchall()]
        cur.execute('SELECT source,query,observation_date::text,frequency FROM public.pulse_search_demand_observations ORDER BY observation_date')
        observations = [dict(r) for r in cur.fetchall()]
    return baskets, observations
