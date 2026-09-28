"""Known-source money gate for dated product contribution (not full net profit)."""
from unit_economics_engine import decimal


def dated_contribution(row):
    if row.get('missing_amount') or not row.get('historical_cogs_covered'):
        return None
    net, units = decimal(row.get('net')), decimal(row.get('units'))
    cogs = decimal((row.get('cogs') or {}).get('amount'))
    if any(value is None or not value.is_finite() for value in (net, units, cogs)) or cogs < 0:
        return None
    # Zero/negative net units are meaningful period flows, not per-unit forecasts.
    return net - units * cogs
