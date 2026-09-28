#!/usr/bin/env python3
"""Import the two-client registry from JSON stdin; never print credentials."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pulse_vps_admin


ALLOWED = {"toptop", "lera_nena"}


def main() -> None:
    payload = json.load(sys.stdin)
    clients = payload.get("clients") if isinstance(payload, dict) else None
    if not isinstance(clients, list) or {str(item.get("key")) for item in clients} != ALLOWED:
        raise ValueError("registry payload must contain exactly TOPTOP and LERA NENA")
    pulse_vps_admin.configure_scope()
    import client_registry

    client_registry.DEFAULT_CLIENTS_ROOT = Path("/var/lib/pulse/clients")
    master_key = pulse_vps_admin._secret("PULSE_CREDENTIALS_MASTER_KEY_FILE")
    credential_count = account_count = store_count = 0
    with pulse_vps_admin.app.client_registry_connection() as conn:
        client_registry.ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT client_key FROM public.bi_client_registry WHERE client_key <> ALL(%s)", (list(ALLOWED),))
            foreign = [row[0] for row in cur.fetchall()]
        if foreign:
            raise RuntimeError("foreign clients already exist; refusing import")
        for raw in clients:
            key = str(raw["key"])
            config = pulse_vps_admin.app.ADMIN_CLIENTS[key]
            client_registry.save_client(
                conn,
                {
                    "key": key,
                    "label": config["label"],
                    "db_name": key,
                    "root_path": f"/var/lib/pulse/clients/{key}",
                    "status": "active",
                    "marketplaces": list(config["marketplaces"]),
                    "reports": list(config["reports"]),
                },
                raw.get("credentials") or {},
                master_key,
            )
            credential_count += len(raw.get("credentials") or {})
            for marketplace, accounts in (raw.get("marketplace_accounts") or {}).items():
                enabled = {
                    str(store.get("campaign_id"))
                    for account in accounts for store in (account.get("stores") or [])
                    if store.get("import_enabled", True) and store.get("is_accessible", True)
                }
                client_registry.save_marketplace_accounts(conn, key, marketplace, accounts, enabled)
                account_count += len(accounts)
                store_count += sum(len(account.get("stores") or []) for account in accounts)
    print(
        f"Готово: clients=2 credentials={credential_count} accounts={account_count} stores={store_count}; secrets_output=0",
        flush=True,
    )


if __name__ == "__main__":
    main()
