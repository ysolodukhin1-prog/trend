"""Strict read-only source entitlement for GALACTICA; no shared/admin login fallback."""

import base64
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4


class SourceDenied(PermissionError):
    pass


def session_revision(value):
    """Canonical aware timestamp; preserve microseconds across DB session timezones."""
    try:
        if isinstance(value, str) and len(value) <= 64:
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise SourceDenied()
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError):
        raise SourceDenied() from None


def verified_subject(token, signing_key, now=None, *, audience=None, instance_id=None):
    now = time.time() if now is None else now
    try:
        if not token or len(token) > 4096 or not signing_key:
            raise SourceDenied()
        encoded, signature = token.split(".")
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        expected = hmac.new(signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise SourceDenied()
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if audience is None:
            if "aud" in payload or "instance_id" in payload:
                raise SourceDenied()
        elif payload.get("aud") != audience or payload.get("instance_id") != instance_id:
            raise SourceDenied()
        user_id = payload.get("user_id")
        issued, expires = payload.get("issued_at"), payload.get("expires_at")
        if (
            type(user_id) is not int
            or user_id <= 0
            or payload.get("is_admin")
            or type(issued) is not int
            or type(expires) is not int
            or issued > now + 60
            or expires <= now
            or expires <= issued
            or expires - issued > 12 * 60 * 60
            or not isinstance(payload.get("username"), str)
        ):
            raise SourceDenied()
        revision = session_revision(payload.get("user_revision"))
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce):
            raise SourceDenied()
        session_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        return user_id, payload["username"], expires, revision, session_hash
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise SourceDenied() from None


def read_entitlement(connection, subject, client, report):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", client) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]{0,119}", report
    ):
        raise SourceDenied()
    user_id, username, expires, token_revision, session_hash = subject
    # Single statement, no list_users/ensure_schema or default-client normalization.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.user_id,u.username,u.updated_at
            FROM public.bi_users u
            WHERE u.user_id=%s AND u.username=%s AND u.is_active
              AND EXISTS (SELECT 1 FROM public.bi_personal_sessions s
                          WHERE s.session_hash=%s AND s.user_id=u.user_id
                          AND s.user_revision=u.updated_at AND s.revoked_at IS NULL
                          AND s.expires_at >= to_timestamp(%s)
                          AND s.expires_at > clock_timestamp())
              AND EXISTS (SELECT 1 FROM public.bi_personal_sessions child
                WHERE child.session_hash=%s AND (child.parent_session_hash IS NULL OR EXISTS (
                  SELECT 1 FROM public.bi_personal_sessions parent
                  WHERE parent.session_hash=child.parent_session_hash
                    AND parent.parent_session_hash IS NULL AND parent.user_id=child.user_id
                    AND parent.user_revision=child.user_revision AND parent.revoked_at IS NULL
                    AND parent.expires_at>=child.expires_at
                    AND parent.expires_at>clock_timestamp())))
              AND EXISTS (SELECT 1 FROM public.bi_user_clients c
                          WHERE c.user_id=u.user_id AND c.client_key=%s)
              AND EXISTS (SELECT 1 FROM public.bi_user_reports r
                          WHERE r.user_id=u.user_id AND r.report_id=%s)
        """,
            (user_id, username, session_hash, expires, session_hash, client, report),
        )
        row = cursor.fetchone()
    if row is None:
        raise SourceDenied()
    values = list(row.values()) if isinstance(row, dict) else list(row)
    current_revision = row["updated_at"] if isinstance(row, dict) else row[2]
    if session_revision(current_revision) != token_revision:
        raise SourceDenied()
    revision = int.from_bytes(
        hashlib.sha256(
            json.dumps([*map(str, values), client, report], separators=(",", ":")).encode()
        ).digest()[:8],
        "big",
    ) & ((1 << 63) - 1)
    return {
        "allowed": True,
        "source_subject_key": str(user_id),
        "client_key": client,
        "report_id": report,
        "action": "read",
        "entitlement_revision": revision,
        "session_expires_at": expires,
    }


def configured_subject(app, token):
    if not str(
        os.environ.get("DASHBOARD_USER_SESSION_SECRET")
        or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
        or ""
    ).strip():
        raise SourceDenied()
    try:
        return verified_subject(token, app._managed_access_signing_key())
    except SourceDenied:
        instance, key = connector_signing_key(app)
        return verified_subject(token, key, audience="galactica-source", instance_id=instance)


def connector_signing_key(app):
    """Domain-separated key: connector credentials cannot pass normal browser verification."""
    try:
        value = os.environ.get("PULSE_GALACTICA_INSTANCE_ID", "")
        instance = UUID(value)
        if not instance.int or str(instance) != value:
            raise SourceDenied()
        if not str(
            os.environ.get("DASHBOARD_USER_SESSION_SECRET")
            or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
            or ""
        ).strip():
            raise SourceDenied()
        key = hmac.new(
            app._managed_access_signing_key(),
            b"galactica-source-credential-v1\x00" + instance.bytes,
            hashlib.sha256,
        ).digest()
        return value, key
    except (ValueError, TypeError):
        raise SourceDenied() from None


def transaction_limits(connection):
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cursor.execute("SET LOCAL statement_timeout='2s'")
        cursor.execute("SET LOCAL lock_timeout='500ms'")


def verify_report_delivery_schema(connection):
    """Reject partial/disabled source fences while authority tables are locked."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT count(*) FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname IN "
            "('bi_users','bi_personal_sessions','bi_user_clients','bi_user_reports',"
            "'bi_client_registry') "
            "AND t.tgenabled='A' "
            "AND t.tgfoid='public.bi_report_delivery_authority_fence()'::regprocedure "
            "AND ((t.tgname='bi_report_delivery_fence_write' AND t.tgtype=30) "
            "OR (t.tgname='bi_report_delivery_fence_truncate' AND t.tgtype=34))) "
            "AS authority_triggers, "
            "(SELECT count(*) FROM pg_catalog.pg_trigger t "
            "WHERE t.tgrelid='public.bi_report_delivery_permits'::regclass "
            "AND t.tgenabled='A' "
            "AND ((t.tgname='bi_report_delivery_permit_immutable' AND t.tgtype=27 "
            "AND t.tgfoid='public.bi_report_delivery_permit_immutable()'::regprocedure) "
            "OR (t.tgname='bi_report_delivery_permit_no_truncate' AND t.tgtype=34 "
            "AND t.tgfoid='public.bi_report_delivery_permit_no_truncate()'::regprocedure))) "
            "AS permit_triggers"
        )
        row = cursor.fetchone()
    counts = (
        (row["authority_triggers"], row["permit_triggers"])
        if isinstance(row, dict) else tuple(row) if row else None
    )
    if counts != (10, 2):
        raise SourceDenied()


def require_drained_report_startup(app):
    """Source-mode startup is forbidden while a prior delivery is unresolved.

    Operators must first quiesce the previous source ingress; this exclusive
    lock and durable-marker read then form the final pre-bind drain check.
    """
    with app.client_registry_connection() as connection:
        transaction_limits(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(17491,60)")
        verify_report_delivery_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM public.bi_report_delivery_permits "
                "WHERE closed_at IS NULL)"
            )
            row = cursor.fetchone()
            active = row.get("exists") if isinstance(row, dict) else row[0] if row else True
        if active:
            raise SourceDenied()


class ReportDeliveryPermit:
    """A committed source marker; retention is explicit after a successful send."""

    def __init__(self, permit_id):
        self.permit_id = permit_id
        self.retained = False

    def retain_for_downstream(self):
        self.retained = True


@contextmanager
def report_delivery_guard(app, subject, client, report, expected_revision, *, permit_id=None):
    """Fence final delivery with a durable marker in the canonical source registry.

    The separate committed permit survives loss of the shared-lock backend.
    Source authority writers must reject unresolved permits via the migration
    trigger; missing migration or either database connection fails closed.
    """
    user_id, username, expires, revision, session_hash = subject
    permit = ReportDeliveryPermit(str(permit_id) if permit_id is not None else str(uuid4()))
    permit_requested = False
    with app.client_registry_connection() as connection:
        transaction_limits(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock_shared(17491,60)")
            with app.client_registry_connection() as permit_db:
                transaction_limits(permit_db)
                with permit_db.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO public.bi_report_delivery_permits "
                        "(permit_id,user_id,source_session_hash) VALUES (%s::uuid,%s,%s)",
                        (permit.permit_id, user_id, session_hash),
                    )
                    permit_requested = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT updated_at FROM public.bi_users "
                    "WHERE user_id=%s AND username=%s AND is_active FOR SHARE",
                    (user_id, username),
                )
                row = cursor.fetchone()
                current = (
                    row.get("updated_at") if isinstance(row, dict)
                    else row[0] if row else None
                )
                if session_revision(current) != revision:
                    raise SourceDenied()
                cursor.execute(
                    "SELECT parent_session_hash FROM public.bi_personal_sessions "
                    "WHERE session_hash=%s AND user_id=%s "
                    "AND user_revision=%s::timestamptz AND revoked_at IS NULL "
                    "AND expires_at>=to_timestamp(%s) AND expires_at>clock_timestamp() "
                    "FOR SHARE",
                    (session_hash, user_id, revision, expires),
                )
                row = cursor.fetchone()
                if row is None:
                    raise SourceDenied()
                parent_hash = row.get("parent_session_hash") if isinstance(row, dict) else row[0]
                if parent_hash is not None:
                    cursor.execute(
                        "SELECT 1 FROM public.bi_personal_sessions "
                        "WHERE session_hash=%s AND parent_session_hash IS NULL "
                        "AND user_id=%s AND user_revision=%s::timestamptz "
                        "AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR SHARE",
                        (parent_hash, user_id, revision),
                    )
                    if cursor.fetchone() is None:
                        raise SourceDenied()
                cursor.execute(
                    "SELECT 1 FROM public.bi_user_clients "
                    "WHERE user_id=%s AND client_key=%s FOR SHARE",
                    (user_id, client),
                )
                if cursor.fetchone() is None:
                    raise SourceDenied()
                cursor.execute(
                    "SELECT 1 FROM public.bi_user_reports "
                    "WHERE user_id=%s AND report_id=%s FOR SHARE",
                    (user_id, report),
                )
                if cursor.fetchone() is None:
                    raise SourceDenied()
            verify_report_delivery_schema(connection)
            current = read_entitlement(connection, subject, client, report)
            if current["entitlement_revision"] != expected_revision or expires <= time.time():
                raise SourceDenied()
            yield permit
        finally:
            if permit_requested and not permit.retained:
                with app.client_registry_connection() as permit_db:
                    transaction_limits(permit_db)
                    with permit_db.cursor() as cursor:
                        cursor.execute(
                            "UPDATE public.bi_report_delivery_permits "
                            "SET closed_at=clock_timestamp() "
                            "WHERE permit_id=%s::uuid AND closed_at IS NULL",
                            (permit.permit_id,),
                        )


def register_session(app, token):
    """Call only for a just-authenticated personal login; commit before sending cookie."""
    user_id, username, expires, revision, session_hash = configured_subject(app, token)
    with app.client_registry_connection() as connection:
        transaction_limits(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT updated_at FROM public.bi_users "
                "WHERE user_id=%s AND username=%s AND is_active FOR UPDATE",
                (user_id, username),
            )
            row = cursor.fetchone()
            current = row.get("updated_at") if isinstance(row, dict) else row[0] if row else None
            if session_revision(current) != revision:
                raise SourceDenied()
            cursor.execute(
                "INSERT INTO public.bi_personal_sessions "
                "(session_hash,user_id,user_revision,expires_at) "
                "VALUES (%s,%s,%s,to_timestamp(%s))",
                (session_hash, user_id, revision, expires),
            )


def require_current_session(connection, subject):
    user_id, username, expires, revision, session_hash = subject
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM public.bi_personal_sessions s "
            "JOIN public.bi_users u ON u.user_id=s.user_id "
            "WHERE s.session_hash=%s AND s.user_id=%s AND u.username=%s "
            "AND u.is_active AND u.updated_at=%s::timestamptz "
            "AND s.user_revision=u.updated_at AND s.revoked_at IS NULL "
            "AND s.expires_at>=to_timestamp(%s) AND s.expires_at>clock_timestamp() "
            "AND (s.parent_session_hash IS NULL OR EXISTS ("
            "SELECT 1 FROM public.bi_personal_sessions parent "
            "WHERE parent.session_hash=s.parent_session_hash "
            "AND parent.parent_session_hash IS NULL "
            "AND parent.user_id=s.user_id AND parent.user_revision=s.user_revision "
            "AND parent.revoked_at IS NULL AND parent.expires_at>=s.expires_at "
            "AND parent.expires_at>clock_timestamp()))",
            (session_hash, user_id, username, revision, expires),
        )
        if cursor.fetchone() is None:
            raise SourceDenied()


def require_access_origin(headers):
    """Pinned configuration, never authority inferred from Host/forwarded headers."""
    origin = headers.get("Origin", "")
    allowed = str(os.environ.get("PULSE_ACCESS_ALLOWED_ORIGINS") or "").split(",")
    if not origin or origin not in {item.strip() for item in allowed if item.strip()}:
        raise SourceDenied()
    parsed = urlsplit(origin)
    if (
        parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or any(c.isspace() for c in origin)
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"})
        )
    ):
        raise SourceDenied()
    if headers.get("Sec-Fetch-Site") == "cross-site":
        raise SourceDenied()


def handle_logout(app, handler):
    try:
        require_access_origin(handler.headers)
        token = app.dashboard_access_session_from_cookie(handler.headers.get("Cookie"))
        try:
            subject = configured_subject(app, token)
        except SourceDenied:
            # Invalid, expired or legacy/admin cookie: clear locally, claim no DB revocation.
            subject = None
        revoked = False
        if subject:
            user_id, _, _, revision, session_hash = subject
            with app.client_registry_connection() as connection:
                transaction_limits(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE public.bi_personal_sessions "
                        "SET revoked_at=COALESCE(revoked_at,clock_timestamp()) "
                        "WHERE session_hash=%s AND user_id=%s "
                        "AND user_revision=%s::timestamptz RETURNING session_hash",
                        (session_hash, user_id, revision),
                    )
                    revoked = cursor.fetchone() is not None
        handler.send_json(
            {"ok": True, "authenticated": False, "session_revoked": revoked},
            headers={"Set-Cookie": handler.dashboard_access_cookie_header(clear=True)},
        )
    except (SourceDenied, ValueError):
        handler.send_json({"ok": False, "reason_code": "SOURCE_ACCESS_DENIED"}, status=403)
    except Exception:
        # Includes unknown COMMIT outcome: never claim successful revoke or automatically retry.
        handler.send_json({"ok": False, "reason_code": "SOURCE_AUTHORITY_UNAVAILABLE"}, status=503)


def handle_entitlement(app, handler, parsed):
    """All errors are generic; route bypasses permissive legacy login/client fallbacks."""
    try:
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=4)
        if set(params) != {"client", "report"} or any(len(v) != 1 for v in params.values()):
            raise SourceDenied()
        client, report = params["client"][0], params["report"][0]
        if client not in app.ADMIN_CLIENTS or client in app.dashboard_excluded_clients():
            raise SourceDenied()
        locked = app.dashboard_locked_client()
        if locked and client != locked:
            raise SourceDenied()
        if not str(
            os.environ.get("DASHBOARD_USER_SESSION_SECRET")
            or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
            or ""
        ).strip():
            raise SourceDenied()
        # Only managed personal session cookie. Basic/global admin headers are not authority.
        token = app.dashboard_access_session_from_cookie(handler.headers.get("Cookie"))
        subject = configured_subject(app, token)
        with app.client_registry_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout='2s'")
                cursor.execute("SET LOCAL lock_timeout='500ms'")
            result = read_entitlement(connection, subject, client, report)
        if subject[2] <= time.time():
            raise SourceDenied()
        handler.send_json(result)
    except (SourceDenied, ValueError):
        handler.send_json({"allowed": False, "reason_code": "SOURCE_ACCESS_DENIED"}, status=403)
    except Exception:
        handler.send_json(
            {"allowed": False, "reason_code": "SOURCE_AUTHORITY_UNAVAILABLE"}, status=503
        )
