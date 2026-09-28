"""Personal source consent. Disabled until trusted startup pins a dedicated origin/key."""

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from html import escape
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from galactica_authorization_codes import SourceCodeTarget, _lock_parent, issue_code
from galactica_entitlement import SourceDenied, transaction_limits, verified_subject
from galactica_exchange_http import _one


@dataclass(frozen=True, slots=True)
class ConsentConfiguration:
    target: SourceCodeTarget
    origin: str
    signing_key: bytes = field(repr=False)

    def __post_init__(self):
        uri = urlsplit(self.origin)
        if (
            not isinstance(self.target, SourceCodeTarget)
            or uri.scheme != "https"
            or not uri.hostname
            or uri.username
            or uri.password
            or uri.path
            or uri.query
            or uri.fragment
            or any(c.isspace() for c in self.origin)
            or not isinstance(self.signing_key, bytes)
            or len(self.signing_key) < 32
        ):
            raise ValueError("Invalid consent configuration")


FIELDS = {
    "request_id",
    "client_id",
    "redirect_uri",
    "state",
    "code_challenge",
    "code_challenge_method",
}
PATH = "/api/galactica/authorize"
LOGIN_PATH = "/login?source=1"
LOGIN_PURPOSE = b"pulse-source-login-v1\x00"


def _login_mac(config, encoded):
    return hmac.new(
        config.signing_key, LOGIN_PURPOSE + encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()


def create_login_continuation(config, values):
    """Navigation only, never an authorization credential or automatic consent."""
    if not isinstance(config, ConsentConfiguration) or set(values) != FIELDS:
        raise SourceDenied()
    _validate(values, config)
    payload = {
        **values,
        "expires": str(int(time.time()) + 300),
        "instance_id": str(config.target.instance_id),
        "origin": config.origin,
    }
    encoded = (
        base64.urlsafe_b64encode(urlencode(sorted(payload.items())).encode("ascii"))
        .decode("ascii")
        .rstrip("=")
    )
    return encoded + "." + _login_mac(config, encoded)


def resume_login_continuation(config, ticket):
    """Authenticate the whole request before returning a fixed, local consent path."""
    if (
        not isinstance(config, ConsentConfiguration)
        or not isinstance(ticket, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,3072}\.[0-9a-f]{64}", ticket)
    ):
        raise SourceDenied()
    encoded, signature = ticket.split(".")
    if not hmac.compare_digest(signature, _login_mac(config, encoded)):
        raise SourceDenied()
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        values = _fields(raw.decode("ascii"), FIELDS | {"expires", "instance_id", "origin"})
        now = int(time.time())
        if (
            base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != encoded
            or urlencode(sorted(values.items())).encode("ascii") != raw
            or values.pop("instance_id") != str(config.target.instance_id)
            or values.pop("origin") != config.origin
            or not re.fullmatch(r"[0-9]{10}", values["expires"])
            or not now < int(values.pop("expires")) <= now + 300
        ):
            raise SourceDenied()
        _validate(values, config)
    except (ValueError, UnicodeError, KeyError) as exc:
        raise SourceDenied() from exc
    return PATH + "?" + urlencode(sorted(values.items()))


def _fields(raw, expected):
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
    result = dict(pairs)
    if len(pairs) != len(result) or set(result) != expected:
        raise SourceDenied()
    return result


def _validate(values, config):
    request = UUID(values["request_id"])
    if (
        not request.int
        or str(request) != values["request_id"]
        or values["client_id"] != config.target.client_id
        or values["redirect_uri"] != config.target.redirect_uri
        or values["code_challenge_method"] != "S256"
        or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{43}", values[key])
            for key in ("state", "code_challenge")
        )
    ):
        raise SourceDenied()
    return request


def _signature(config, values, token):
    # Bind approval to every field and the exact original personal browser session.
    payload = urlencode(sorted(values.items())) + "|" + hashlib.sha256(token.encode()).hexdigest()
    return hmac.new(config.signing_key, payload.encode(), hashlib.sha256).hexdigest()


def _page(handler, status, content, location=None, callback=None):
    raw = (
        "<!doctype html><html lang=ru><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        "<title>Подключение GALACTICA — PULSE</title><body><main>"
        + content
        + "</main></body></html>"
    ).encode()
    handler.send_response(status)
    for name, value in {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'none'; form-action 'self'"
            + (" " + callback if callback else "")
            + "; base-uri 'none'; frame-ancestors 'none'"
        ),
        "Connection": "close",
        "Content-Length": str(len(raw)),
    }.items():
        handler.send_header(name, value)
    if location:
        handler.send_header("Location", location)
    handler.end_headers()
    handler.wfile.write(raw)


def handle_consent(app, handler, parsed):
    """Never call dashboard_access_identity: it includes shared/admin fallbacks.

    Missing/revoked personal login may resume only a validated GET via a signed,
    short-lived fragment. POST still requires the exact personal approval session.
    No automatic code reissue on uncertain commit or expired approval.
    """
    handler.close_connection = True
    try:
        config = getattr(app, "GALACTICA_CONSENT_CONFIG", None)
        if not isinstance(config, ConsentConfiguration):
            _page(handler, 404, "<h1>Подключение недоступно</h1>")
            return
        if (
            parsed.path != PATH
            or parsed.fragment
            or _one(handler.headers, "Host") != urlsplit(config.origin).netloc
            or any(
                handler.headers.get_all(key, [])
                for key in ("Authorization", "Transfer-Encoding", "Content-Encoding")
            )
        ):
            raise SourceDenied()
        values = None
        if handler.command == "GET":
            if len(parsed.query) > 2048 or handler.headers.get_all("Content-Length", []):
                raise SourceDenied()
            values = _fields(parsed.query, FIELDS)
            _validate(values, config)
        cookies = handler.headers.get_all("Cookie", [])
        if len(cookies) > 1:
            raise SourceDenied()
        cookie = cookies[0] if cookies else ""
        # Reject ambiguous duplicate session cookies before the legacy cookie parser.
        name = app.DASHBOARD_ACCESS_COOKIE_NAME
        count = sum(part.strip().partition("=")[0].strip() == name for part in cookie.split(";"))
        if count > 1:
            raise SourceDenied()
        try:
            if count != 1:
                raise SourceDenied()
            token = app.dashboard_access_session_from_cookie(cookie)
            subject = verified_subject(token, app._managed_access_signing_key())
            with app.client_registry_connection() as db:
                transaction_limits(db)
                _lock_parent(db, subject)
        except SourceDenied:
            if values is None:
                raise
            location = LOGIN_PATH + "#source_request=" + create_login_continuation(config, values)
            _page(handler, 303, "<h1>Войдите в личную учётную запись PULSE</h1>", location)
            return
        if handler.command == "GET":
            values["expires"] = str(int(time.time()) + 300)
            values["approval"] = _signature(config, values, token)
            hidden = "".join(
                '<input type=hidden name="'
                + escape(k, quote=True)
                + '" value="'
                + escape(v, quote=True)
                + '">'
                for k, v in values.items()
            )
            _page(
                handler,
                200,
                "<h1>Подключить PULSE к GALACTICA</h1><p>Личная учётная запись: <strong>"
                + escape(subject[1])
                + "</strong> (ID "
                + str(subject[0])
                + ")</p><p>Назначение: "
                + escape(config.target.redirect_uri)
                + "</p><p>Подключение не расширяет ваши права. "
                "Сервисы и клиенты дополнительно ограничиваются в GALACTICA.</p>"
                '<form method=post action="'
                + PATH
                + '">'
                + hidden
                + "<button name=decision value=approve>Подключить PULSE</button> "
                "<button name=decision value=cancel>Отменить подключение</button></form>",
                callback=config.target.redirect_uri,
            )
            return
        if (
            handler.command != "POST"
            or parsed.query
            or _one(handler.headers, "Origin") != config.origin
            or _one(handler.headers, "Content-Type") != "application/x-www-form-urlencoded"
        ):
            raise SourceDenied()
        length = _one(handler.headers, "Content-Length")
        if not re.fullmatch(r"[1-9][0-9]{0,3}", length) or int(length) > 4096:
            raise SourceDenied()
        handler.connection.settimeout(5)
        raw = handler.rfile.read(int(length))
        if len(raw) != int(length):
            raise SourceDenied()
        values = _fields(raw.decode("ascii"), FIELDS | {"expires", "approval", "decision"})
        decision, approval = values.pop("decision"), values.pop("approval")
        request = _validate(values, config)
        if (
            decision not in {"approve", "cancel"}
            or not re.fullmatch(r"[0-9]{10}", values["expires"])
            or not int(time.time()) < int(values["expires"]) <= int(time.time()) + 300
            or not hmac.compare_digest(approval, _signature(config, values, token))
        ):
            raise SourceDenied()
        if decision == "cancel":
            _page(
                handler,
                200,
                "<h1>Подключение отменено</h1><p>Вернитесь в SFERA и отмените заявку.</p>",
            )
            return
        code = issue_code(
            app,
            config.target,
            request_id=request,
            pkce_challenge=values["code_challenge"],
            parent_token=token,
        )
        # Fragment stays out of the callback HTTP request/access log. SFERA must clear
        # it before sending same-origin POST under the original browser credential.
        location = (
            config.target.redirect_uri
            + "#"
            + urlencode({"code": code, "state": values["state"], "request_id": str(request)})
        )
        _page(handler, 303, "<h1>Возврат в SFERA</h1>", location, config.target.redirect_uri)
    except (SourceDenied, ValueError, TypeError):
        _page(
            handler,
            403,
            "<h1>Подключение запрещено</h1><p>Войдите в личную учётную запись PULSE "
            "и начните подключение заново в SFERA.</p>",
        )
    except Exception:
        _page(
            handler,
            503,
            "<h1>Результат не подтверждён</h1><p>Не повторяйте отправку. "
            "Требуется проверка состояния подключения.</p>",
        )
