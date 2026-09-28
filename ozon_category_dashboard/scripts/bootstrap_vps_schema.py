#!/usr/bin/env python3
"""Idempotently initialize scoped registry, users, and managed sessions."""

from __future__ import annotations

from pathlib import Path

import pulse_vps_admin


def main() -> None:
    pulse_vps_admin.configure_scope()
    import client_registry
    import user_registry

    with pulse_vps_admin.app.client_registry_connection() as conn:
        client_registry.ensure_schema(conn)
        user_registry.ensure_schema(conn)
        for migration in (
            "20260912_pulse_personal_sessions.sql",
            "20260913_pulse_delegated_sessions.sql",
        ):
            sql = (Path("/workspace/migrations") / migration).read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
    print("PULSE schema ready: registry + users + managed sessions", flush=True)


if __name__ == "__main__":
    main()
