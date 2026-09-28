"""Current personal identity observation for server-side GALACTICA provisioning."""

import os
import re
import time
from urllib.parse import parse_qs
from uuid import UUID

from galactica_entitlement import (
    SourceDenied,
    configured_subject,
    require_current_session,
    transaction_limits,
)


def handle_identity(app, handler, parsed):
    """No identity discovery, grants or token exchange; only validate supplied session."""
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    try:
        instance = os.environ.get("PULSE_GALACTICA_INSTANCE_ID", "")
        if not instance or str(UUID(instance)) != instance or UUID(instance).int == 0:
            raise SourceDenied()
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
        if set(params) != {"instance", "challenge"} or any(
            len(values) != 1 for values in params.values()
        ):
            raise SourceDenied()
        challenge = params["challenge"][0]
        if params["instance"][0] != instance or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
            raise SourceDenied()
        token = app.dashboard_access_session_from_cookie(handler.headers.get("Cookie"))
        subject = configured_subject(app, token)
        with app.client_registry_connection() as connection:
            transaction_limits(connection)
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            require_current_session(connection, subject)
        if subject[2] <= time.time():
            raise SourceDenied()
        handler.send_json(
            {
                "verified": True,
                "instance_id": instance,
                "challenge": challenge,
                "source_subject_key": str(subject[0]),
                "source_user_revision": subject[3],
                "session_expires_at": subject[2],
            },
            headers=headers,
        )
    except (SourceDenied, ValueError, TypeError):
        handler.send_json(
            {"verified": False, "reason_code": "SOURCE_IDENTITY_DENIED"},
            status=403,
            headers=headers,
        )
    except Exception:
        handler.send_json(
            {"verified": False, "reason_code": "SOURCE_AUTHORITY_UNAVAILABLE"},
            status=503,
            headers=headers,
        )
