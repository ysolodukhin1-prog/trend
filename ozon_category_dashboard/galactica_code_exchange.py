"""Trusted back-channel exchange. Never expose returned credential to browser JSON."""

import base64
import hashlib
import hmac
import json
import secrets
import time

from galactica_authorization_codes import SourceCodeTarget, redeem_code
from galactica_entitlement import SourceDenied, connector_signing_key, require_current_session


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def exchange_code(app, target, *, request_id, code, verifier):
    """Mint exactly one child and consume its code in the same source transaction.

    Caller is the future authenticated BFF exchange endpoint, not browser code.
    Unknown COMMIT propagates without delivering a credential or issuing a second one;
    immutable code->session receipt permits subsequent explicit revoke/reconciliation.
    No network/disk side effects occur inside this transaction.
    """
    instance, key = connector_signing_key(app)
    if not isinstance(target, SourceCodeTarget) or str(target.instance_id) != instance:
        raise SourceDenied()
    with redeem_code(app, target, request_id=request_id, code=code, verifier=verifier) as (
        db,
        parent,
    ):
        issued = int(time.time())
        expires = min(parent[2], issued + 43200)
        if expires <= issued:
            raise SourceDenied()
        nonce = secrets.token_urlsafe(32)
        payload = {
            "user_id": parent[0],
            "username": parent[1],
            "is_admin": False,
            "user_revision": parent[3],
            "issued_at": issued,
            "expires_at": expires,
            "nonce": nonce,
            "aud": "galactica-source",
            "instance_id": instance,
        }
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        credential = (
            encoded + "." + _b64(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
        )
        session_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.bi_personal_sessions "
                "(session_hash,user_id,user_revision,expires_at,parent_session_hash) "
                "VALUES (%s,%s,%s::timestamptz,to_timestamp(%s),%s)",
                (session_hash, parent[0], parent[3], expires, parent[4]),
            )
            cursor.execute(
                "INSERT INTO public.bi_galactica_code_credentials(code_hash,session_hash) "
                "VALUES (%s,%s)",
                (hashlib.sha256(code.encode("ascii")).hexdigest(), session_hash),
            )
        require_current_session(db, (parent[0], parent[1], expires, parent[3], session_hash))
    return credential
