"""Exact-client, bounded, read-only ABC report entry point for the source connector."""

import json
import re
import socket
import time
from contextlib import contextmanager
from datetime import date
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID

from galactica_entitlement import (
    SourceDenied,
    configured_subject,
    connector_signing_key,
    read_entitlement,
    report_delivery_guard,
    transaction_limits,
    verified_subject,
)

MAX_RESPONSE_BYTES = 1024 * 1024
DELIVERY_SECONDS = 5
PARAMETERS = {
    "client",
    "report",
    "operation",
    "marketplace",
    "date_from",
    "date_to",
    "category_exact",
    "category",
    "limit",
    "page",
    "delivery_id",
}


def abort_report_response(handler):
    """Never append a second HTTP response after protected emission has begun."""
    handler.close_connection = True
    connection = getattr(handler, "connection", None)
    if connection is not None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass


def send_report_response(handler, payload, body, expires_at, started):
    """One monotonic deadline covers headers, body and flush on the real socket."""
    connection = getattr(handler, "connection", None)
    if connection is None:
        started[0] = True
        handler.send_json(payload)
        return
    deadline = time.monotonic() + min(DELIVERY_SECONDS, max(0.0, expires_at - time.time()))
    previous_timeout = connection.gettimeout()

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("source report delivery deadline")
        connection.settimeout(value)

    completed = False
    try:
        remaining()
        started[0] = True
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store, max-age=0")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Content-Length", str(len(body)))
        remaining()
        handler.end_headers()
        remaining()
        written = handler.wfile.write(body)
        if written != len(body):
            raise OSError("short source report write")
        remaining()
        handler.wfile.flush()
        completed = True
    finally:
        if completed or not started[0]:
            connection.settimeout(previous_timeout)


def parse_report(parsed):
    if len(parsed.query) > 4096:
        raise SourceDenied()
    query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
    if set(query) - PARAMETERS or any(len(v) != 1 or not v[0] for v in query.values()):
        raise SourceDenied()
    params = {key: value[0] for key, value in query.items()}
    if (
        not {"client", "report", "operation", "marketplace"} <= set(params)
        or params["report"] != "abc"
        or params["operation"] not in {"summary", "stats"}
        or params["marketplace"] not in {"wb", "ozon"}
    ):
        raise SourceDenied()
    if ("date_from" in params) != ("date_to" in params):
        raise SourceDenied()
    if "date_from" in params:
        start, end = date.fromisoformat(params["date_from"]), date.fromisoformat(params["date_to"])
        if start > end or (end - start).days > 366:
            raise SourceDenied()
    if "category" in params and "category_exact" in params:
        raise SourceDenied()
    if any(len(params.get(key, "")) > 240 for key in ("category", "category_exact")):
        raise SourceDenied()
    for key, maximum in (("limit", 200), ("page", 1000)):
        if key in params and (
            not params[key].isascii()
            or not params[key].isdigit()
            or not 1 <= int(params[key]) <= maximum
        ):
            raise SourceDenied()
    if "limit" in params and int(params["limit"]) < 5:
        raise SourceDenied()
    if params["operation"] == "summary" and {"limit", "page"} & set(params):
        raise SourceDenied()
    if "delivery_id" in params:
        try:
            if str(UUID(params["delivery_id"])) != params["delivery_id"]:
                raise SourceDenied()
        except (ValueError, TypeError):
            raise SourceDenied() from None
    return params


def authorize(app, subject, client):
    if (
        client not in app.ADMIN_CLIENTS
        or client in app.dashboard_excluded_clients()
        or (app.dashboard_locked_client() and app.dashboard_locked_client() != client)
    ):
        raise SourceDenied()
    runtime = app.ADMIN_CLIENTS[client]
    if runtime.get("status", "active") != "active":
        raise SourceDenied()
    with app.client_registry_connection() as db:
        with db.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout='2s'")
            cursor.execute("SET LOCAL lock_timeout='500ms'")
            cursor.execute(
                "SELECT db_name,status,marketplaces,reports,updated_at "
                "FROM public.bi_client_registry WHERE client_key=%s", (client,)
            )
            row = cursor.fetchone()
        runtime_contract = (
            runtime.get("db_name"), runtime.get("status", "active"),
            tuple(runtime.get("marketplaces") or ()), tuple(runtime.get("reports") or ()),
        )
        if row is None:
            registry_marker = None
        else:
            values = tuple(row.values()) if isinstance(row, dict) else tuple(row)
            canonical_contract = (
                values[0], values[1], tuple(values[2] or ()), tuple(values[3] or ()),
            )
            if canonical_contract != runtime_contract or "abc" not in canonical_contract[3]:
                raise SourceDenied()
            registry_marker = str(values[4])
        entitlement = read_entitlement(db, subject, client, "abc")
        # Compare before and after report generation. The source permit blocks
        # later registry commits; this marker also catches commit→in-memory
        # publication races that happened before the permit was inserted.
        return dict(
            entitlement, _client_contract=(runtime_contract, registry_marker)
        )


@contextmanager
def exact_report_connection(app, config):
    connection = app.psycopg2.connect(**config, cursor_factory=app.RealDictCursor)
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection:
            yield connection
    finally:
        connection.close()


def handle_report(app, handler, parsed):
    response_started = [False]
    try:
        params = parse_report(parsed)
        client, operation = params["client"], params["operation"]
        credential = app.dashboard_access_session_from_cookie(handler.headers.get("Cookie"))
        subject = configured_subject(app, credential)
        delivery_id = params.get("delivery_id")
        if delivery_id:
            instance_id, key = connector_signing_key(app)
            if verified_subject(
                credential, key, audience="galactica-source", instance_id=instance_id
            ) != subject:
                raise SourceDenied()
        before = authorize(app, subject, client)
        if params["marketplace"] not in app.client_marketplace_ids(client):
            raise SourceDenied()
        # Set both contexts before read_db_config; still independently compare exact DB name.
        identity = {
            "user_id": subject[0],
            "username": subject[1],
            "is_admin": False,
            "clients": [client],
            "reports": ["abc"],
            "admin_sections": [],
        }
        access_context = app.CURRENT_ACCESS_USER.set(identity)
        client_context = app.CURRENT_CLIENT.set(client)
        try:
            expected_db = app.ADMIN_CLIENTS[client]["db_name"]
            config = dict(app.read_db_config(client))
            if not expected_db or config.get("database") != expected_db:
                raise SourceDenied()
            config["connect_timeout"] = 3
            config["options"] = (
                str(config.get("options", ""))
                + " -c default_transaction_read_only=on"
                " -c statement_timeout=3000 -c lock_timeout=500"
            )
            factory_context = app.CURRENT_GALACTICA_REPORT_CONNECTION.set(
                lambda: exact_report_connection(app, config)
            )
            try:
                report_query = {k: v for k, v in params.items() if k not in {"operation", "report", "delivery_id"}}
                report_parsed = urlparse("/api/" + operation + "?" + urlencode(report_query))
                callback = app.handle_summary if operation == "summary" else app.handle_stats
                data = callback(report_parsed)
            finally:
                app.CURRENT_GALACTICA_REPORT_CONNECTION.reset(factory_context)
        finally:
            app.CURRENT_CLIENT.reset(client_context)
            app.CURRENT_ACCESS_USER.reset(access_context)
        payload = {
            "source_subject_key": str(subject[0]),
            "client_key": client,
            "report_id": "abc",
            "operation": operation,
            "marketplace": params["marketplace"],
            "source_entitlement_revision": before["entitlement_revision"],
            "filters": report_query,
            "data": data,
        }
        if delivery_id:
            payload["delivery_id"] = delivery_id
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            raise SourceDenied()
        # Revalidate after report generation and bounded serialization. The
        # separate durable permit survives shared-lock backend loss; release
        # still requires migration, fault tests and independent acceptance.
        with report_delivery_guard(
            app, subject, client, "abc", before["entitlement_revision"],
            permit_id=delivery_id,
        ) as permit:
            after = authorize(app, subject, client)
            if after != before or subject[2] <= time.time():
                raise SourceDenied()
            send_report_response(handler, payload, body, subject[2], response_started)
            if delivery_id:
                permit.retain_for_downstream()
    except (SourceDenied, ValueError):
        if response_started[0]:
            abort_report_response(handler)
            return
        handler.send_json({"reason_code": "SOURCE_ACCESS_DENIED"}, status=403)
    except Exception:
        if response_started[0]:
            abort_report_response(handler)
            return
        handler.send_json({"reason_code": "SOURCE_REPORT_UNAVAILABLE"}, status=503)


def handle_delivery_finish(app, handler, parsed):
    """Close only the exact delegated session's retained source permit."""
    try:
        if parsed.query:
            raise SourceDenied()
        length = handler.headers.get("Content-Length", "")
        if (
            not isinstance(length, str)
            or not re.fullmatch(r"[1-9][0-9]{0,2}", length)
            or int(length) > 512
            or handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise SourceDenied()
        body = handler.read_json_body()
        if (
            not isinstance(body, dict)
            or set(body) != {"delivery_id"}
            or not isinstance(body["delivery_id"], str)
        ):
            raise SourceDenied()
        delivery_id = str(UUID(body["delivery_id"]))
        if body["delivery_id"] != delivery_id:
            raise SourceDenied()
        credential = app.dashboard_access_session_from_cookie(handler.headers.get("Cookie"))
        instance_id, key = connector_signing_key(app)
        subject = verified_subject(
            credential, key, audience="galactica-source", instance_id=instance_id
        )
        with app.client_registry_connection() as db:
            transaction_limits(db)
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id,source_session_hash,closed_at "
                    "FROM public.bi_report_delivery_permits "
                    "WHERE permit_id=%s::uuid FOR UPDATE",
                    (delivery_id,),
                )
                row = cursor.fetchone()
                values = list(row.values()) if isinstance(row, dict) else row
                if values is None or values[0] != subject[0] or values[1] != subject[4]:
                    raise SourceDenied()
                if values[2] is None:
                    cursor.execute(
                        "UPDATE public.bi_report_delivery_permits "
                        "SET closed_at=clock_timestamp() WHERE permit_id=%s::uuid",
                        (delivery_id,),
                    )
        handler.send_json({"closed": True, "delivery_id": delivery_id})
    except (SourceDenied, ValueError, TypeError):
        handler.send_json({"reason_code": "SOURCE_ACCESS_DENIED"}, status=403)
    except Exception:
        handler.send_json({"reason_code": "SOURCE_REPORT_UNAVAILABLE"}, status=503)
