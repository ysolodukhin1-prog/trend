"""Trusted source-side enrollment after consent/exchange; never a public token API."""

from galactica_entitlement import (
    SourceDenied,
    configured_subject,
    require_current_session,
    transaction_limits,
)


def register_delegated_session(app, parent_token, delegated_token):
    """Enroll a freshly signed child only under a still-current personal root.

    Tokens remain transient and must never be logged. This function does not issue
    credentials or prove consent; only the reviewed authorization exchange may call it.
    Database failures (including unknown COMMIT) propagate without a success claim.
    Replay never reactivates a revoked child or replaces its immutable parent.
    """
    parent = configured_subject(app, parent_token)
    child = configured_subject(app, delegated_token)
    if (
        parent[:2] != child[:2]
        or parent[3] != child[3]
        or child[2] > parent[2]
        or child[4] == parent[4]
    ):
        raise SourceDenied()
    with app.client_registry_connection() as connection:
        transaction_limits(connection)
        with connection.cursor() as cursor:
            # Lock the user then the root session: hold parent logout/user changes
            # until enrollment commits, and verify the current state after each lock.
            cursor.execute(
                "SELECT user_id FROM public.bi_users WHERE user_id=%s FOR SHARE", (parent[0],)
            )
            cursor.execute(
                "SELECT session_hash FROM public.bi_personal_sessions "
                "WHERE session_hash=%s AND parent_session_hash IS NULL FOR SHARE",
                (parent[4],),
            )
            if cursor.fetchone() is None:
                raise SourceDenied()
            require_current_session(connection, parent)
            cursor.execute(
                "INSERT INTO public.bi_personal_sessions "
                "(session_hash,user_id,user_revision,expires_at,parent_session_hash) "
                "VALUES (%s,%s,%s::timestamptz,to_timestamp(%s),%s) "
                "ON CONFLICT (session_hash) DO NOTHING",
                (child[4], child[0], child[3], child[2], parent[4]),
            )
            cursor.execute(
                "SELECT 1 FROM public.bi_personal_sessions WHERE session_hash=%s "
                "AND parent_session_hash=%s AND user_id=%s AND user_revision=%s::timestamptz "
                "AND expires_at=to_timestamp(%s) AND revoked_at IS NULL",
                (child[4], parent[4], child[0], child[3], child[2]),
            )
            if cursor.fetchone() is None:
                raise SourceDenied()
            require_current_session(connection, child)
