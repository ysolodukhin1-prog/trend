"""Dedicated authenticated back-channel; disabled without explicit startup configuration."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from uuid import UUID

from galactica_authorization_codes import SourceCodeTarget
from galactica_code_exchange import exchange_code
from galactica_entitlement import SourceDenied


@dataclass(frozen=True, slots=True)
class ExchangeConfiguration:
    target: SourceCodeTarget
    host: str
    client_secret_sha256: str = field(repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.target, SourceCodeTarget)
            or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]{1,5})?", self.host)
            or not re.fullmatch(r"[0-9a-f]{64}", self.client_secret_sha256)
        ):
            raise ValueError("Invalid exchange configuration")


def _one(headers, name):
    values = headers.get_all(name, [])
    if len(values) != 1:
        raise SourceDenied()
    return values[0]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceDenied()
        result[key] = value
    return result


def handle_exchange(app, handler, parsed):
    """Server only. Never reuse browser cookies or the normal PULSE signing secret.

    Operator must terminate TLS on a fixed protected origin before activation.
    Configuration supplies only a dedicated random client credential's SHA256 hash;
    possession grants exchange capability, not personal/source rights or code issuance.
    Unknown outcome returns generic503 and must be reconciled, never auto-retried.
    """
    handler.close_connection = True
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "Connection": "close"}
    status, result = 403, {"reason_code": "SOURCE_EXCHANGE_DENIED"}
    try:
        config = getattr(app, "GALACTICA_EXCHANGE_CONFIG", None)
        if not isinstance(config, ExchangeConfiguration):
            handler.send_json({"reason_code": "SOURCE_EXCHANGE_UNAVAILABLE"}, 404, headers)
            return
        if parsed.query or parsed.fragment or _one(handler.headers, "Host") != config.host:
            raise SourceDenied()
        if any(
            handler.headers.get_all(name, [])
            for name in (
                "Cookie",
                "Origin",
                "Transfer-Encoding",
                "Content-Encoding",
            )
        ):
            raise SourceDenied()
        authorization = _one(handler.headers, "Authorization")
        if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
            raise SourceDenied()
        digest = hashlib.sha256(authorization[7:].encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, config.client_secret_sha256):
            raise SourceDenied()
        if _one(handler.headers, "Content-Type") != "application/json":
            raise SourceDenied()
        length = _one(handler.headers, "Content-Length")
        if not re.fullmatch(r"[1-9][0-9]{0,3}", length) or int(length) > 2048:
            raise SourceDenied()
        handler.connection.settimeout(5)
        raw = handler.rfile.read(int(length))
        if len(raw) != int(length):
            raise SourceDenied()
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
        if (
            not isinstance(body, dict)
            or set(body)
            != {
                "request_id",
                "code",
                "verifier",
                "client_id",
                "redirect_uri",
                "instance_id",
            }
            or any(not isinstance(value, str) for value in body.values())
        ):
            raise SourceDenied()
        request_id = UUID(body["request_id"])
        if str(request_id) != body["request_id"] or not request_id.int:
            raise SourceDenied()
        if (body["client_id"], body["redirect_uri"], body["instance_id"]) != (
            config.target.client_id,
            config.target.redirect_uri,
            str(config.target.instance_id),
        ):
            raise SourceDenied()
        credential = exchange_code(
            app, config.target, request_id=request_id, code=body["code"], verifier=body["verifier"]
        )
        status, result = 200, {"credential": credential, "request_id": str(request_id)}
    except (SourceDenied, ValueError, TypeError):
        pass
    except Exception:
        status, result = 503, {"reason_code": "SOURCE_EXCHANGE_UNCONFIRMED"}
    handler.send_json(result, status=status, headers=headers)
