"""Source-side single-use code ledger. No HTTP issuer, consent or credential delivery here."""

import base64
import hashlib
import hmac
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from galactica_entitlement import (
    SourceDenied,
    configured_subject,
    require_current_session,
    session_revision,
    transaction_limits,
)


def pkce_s256(verifier):
    if not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise SourceDenied()
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode()
    )


@dataclass(frozen=True, slots=True)
class SourceCodeTarget:
    instance_id: UUID
    client_id: str
    redirect_uri: str

    def __post_init__(self):
        uri = urlsplit(self.redirect_uri)
        if (
            not isinstance(self.instance_id, UUID)
            or not self.instance_id.int
            or not re.fullmatch(r"[a-z][a-z0-9._-]{0,119}", self.client_id)
            or uri.scheme != "https"
            or not uri.hostname
            or not uri.path.startswith("/")
            or uri.username
            or uri.password
            or uri.query
            or uri.fragment
            or len(self.redirect_uri) > 512
            or any(c.isspace() for c in self.redirect_uri)
        ):
            raise ValueError("Invalid fixed source authorization target")


def _lock_parent(connection, subject):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT user_id FROM public.bi_users WHERE user_id=%s FOR SHARE", (subject[0],)
        )
        cursor.execute(
            "SELECT session_hash FROM public.bi_personal_sessions "
            "WHERE session_hash=%s AND parent_session_hash IS NULL FOR SHARE",
            (subject[4],),
        )
        if cursor.fetchone() is None:
            raise SourceDenied()
    require_current_session(connection, subject)


def issue_code(app, target, *, request_id, pkce_challenge, parent_token):
    """Trusted consent handler ONLY, after CSRF and explicit personal approval.

    Return opaque code only after commit. Never log arguments/return value. Retry of
    the same attempt cannot replace a code: uniqueness fails, requiring reconciliation.
    """
    if (
        not isinstance(target, SourceCodeTarget)
        or not isinstance(request_id, UUID)
        or not request_id.int
    ):
        raise SourceDenied()
    if not isinstance(pkce_challenge, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{43}", pkce_challenge
    ):
        raise SourceDenied()
    subject = configured_subject(app, parent_token)
    code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
    with app.client_registry_connection() as connection:
        transaction_limits(connection)
        _lock_parent(connection, subject)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.bi_galactica_authorization_codes
                  (code_hash,request_id,instance_id,client_id,redirect_uri,pkce_challenge,
                   parent_session_hash,user_id,username,user_revision,parent_token_expires_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,to_timestamp(%s),
                        LEAST(clock_timestamp()+interval '59 seconds',to_timestamp(%s)))
            """,
                (
                    code_hash,
                    str(request_id),
                    str(target.instance_id),
                    target.client_id,
                    target.redirect_uri,
                    pkce_challenge,
                    subject[4],
                    subject[0],
                    subject[1],
                    subject[3],
                    subject[2],
                    subject[2],
                ),
            )
    return code


@contextmanager
def redeem_code(app, target, *, request_id, code, verifier):
    """Single transaction: consume + caller's future source enrollment must be atomic.

    Yields (connection, verified parent tuple). Caller must not send a token or make
    external effects until context exits successfully. Failure rolls back consumption;
    uncertain COMMIT propagates, never authorizes automatic reissue. No consumed replay.
    """
    challenge = pkce_s256(verifier)
    if (
        not isinstance(target, SourceCodeTarget)
        or not isinstance(request_id, UUID)
        or not isinstance(code, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
    ):
        raise SourceDenied()
    digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    with app.client_registry_connection() as connection:
        transaction_limits(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id,username,
                       extract(epoch FROM parent_token_expires_at)::bigint AS expires_epoch,
                       user_revision,parent_session_hash,pkce_challenge
                FROM public.bi_galactica_authorization_codes
                WHERE code_hash=%s AND request_id=%s AND instance_id=%s AND client_id=%s
                  AND redirect_uri=%s AND consumed_at IS NULL AND expires_at>clock_timestamp()
                  AND parent_token_expires_at>clock_timestamp() FOR UPDATE
            """,
                (
                    digest,
                    str(request_id),
                    str(target.instance_id),
                    target.client_id,
                    target.redirect_uri,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise SourceDenied()
            if isinstance(row, dict):
                # Require known column ordering independently of driver row-factory.
                row = tuple(
                    row[key]
                    for key in (
                        "user_id",
                        "username",
                        "expires_epoch",
                        "user_revision",
                        "parent_session_hash",
                        "pkce_challenge",
                    )
                )
            if not hmac.compare_digest(row[5], challenge):
                raise SourceDenied()
            subject = (row[0], row[1], int(row[2]), session_revision(row[3]), row[4])
            _lock_parent(connection, subject)
            cursor.execute(
                "UPDATE public.bi_galactica_authorization_codes "
                "SET consumed_at=clock_timestamp() WHERE code_hash=%s",
                (digest,),
            )
            yield connection, subject
            cursor.execute(
                "SELECT 1 FROM public.bi_galactica_authorization_codes "
                "WHERE code_hash=%s AND expires_at>clock_timestamp() "
                "AND parent_token_expires_at>clock_timestamp()",
                (digest,),
            )
            if cursor.fetchone() is None:
                raise SourceDenied()
            require_current_session(connection, subject)
