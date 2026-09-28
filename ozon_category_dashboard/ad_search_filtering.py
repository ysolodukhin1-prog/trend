import json, math

def filter_rows(rows, raw):
    try: filters = json.loads(raw or '[]')
    except (ValueError, TypeError): return rows
    if not isinstance(filters, list): return rows
    def matches(row):
        for f in filters:
            if not isinstance(f, dict): continue
            key, op, wanted = f.get('column'), f.get('op'), str(f.get('value', '')).strip()
            if not wanted or key not in row: continue
            value = row[key]
            if value is None: return False
            if isinstance(value, (int, float)):
                try: target = float(wanted.replace(',', '.'))
                except ValueError: continue
                if not math.isfinite(target): continue
                checks = {'eq': value == target, 'neq': value != target, 'gt': value > target, 'gte': value >= target, 'lt': value < target, 'lte': value <= target}
            else:
                value, wanted = str(value).lower(), wanted.lower()
                checks = {'contains': wanted in value, 'not_contains': wanted not in value, 'eq': value == wanted, 'neq': value != wanted}
            if op in checks and not checks[op]: return False
        return True
    return [r for r in rows if matches(r)]
