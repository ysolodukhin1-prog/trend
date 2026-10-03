"""Local-only login return paths shared by the dashboard entry points."""

from urllib.parse import parse_qs, unquote, urlencode, urlsplit


def safe_return_path(value, default="/"):
    if not isinstance(value, str) or not value.startswith("/"):
        return default
    decoded = unquote(value)
    if decoded.startswith("//") or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return default
    try:
        target = urlsplit(value)
        decoded_path = unquote(target.path)
    except ValueError:
        return default
    if target.scheme or target.netloc or target.fragment:
        return default
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        return default
    if decoded_path.rstrip("/") == "/login" or decoded_path.startswith("/api/"):
        return default
    return value


def scoped_react_entry(path, default_client="toptop"):
    """Canonicalize an entry URL without losing report, client or filters."""
    parsed = urlsplit(path)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == "/" and params.get("dashboard") == ["admin"]:
        return None
    if parsed.path not in {"/", "/react", "/react/"}:
        return None
    if parsed.path == "/react/" and params.get("client", [""])[0].strip():
        return None
    if not params.get("client", [""])[0].strip():
        params["client"] = [default_client]
    params.setdefault("dashboard", ["home"])
    params.setdefault("marketplace", ["ozon"])
    return "/react/?" + urlencode(params, doseq=True)
