"""Create the first full-access VPS PULSE user with an interactive password."""

from __future__ import annotations

import argparse
import getpass

import pulse_vps_admin


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--email", default="")
    args = parser.parse_args()
    pulse_vps_admin.configure_scope()
    from user_registry import active_user_count, save_user

    with pulse_vps_admin.app.client_registry_connection() as conn:
        if active_user_count(conn):
            raise RuntimeError("Первый пользователь уже создан; используйте админку")
        username = args.username or input("Логин: ").strip()
        display_name = args.display_name or input("Имя: ").strip()
        password = getpass.getpass("Новый пароль: ")
        confirmation = getpass.getpass("Повторите пароль: ")
        if password != confirmation:
            raise ValueError("Пароли не совпадают")
        save_user(
            conn,
            {
                "username": username,
                "display_name": display_name,
                "email": args.email,
                "email_verified": False,
                "password": password,
                "clients": sorted(pulse_vps_admin.ALLOWED_CLIENTS),
                "reports": [item["id"] for item in pulse_vps_admin.app.ADMIN_REPORT_CATALOG],
                "admin_sections": sorted(pulse_vps_admin.app.ADMIN_SECTION_IDS),
                "is_active": True,
            },
            set(pulse_vps_admin.ALLOWED_CLIENTS),
            {item["id"] for item in pulse_vps_admin.app.ADMIN_REPORT_CATALOG},
            set(pulse_vps_admin.app.ADMIN_SECTION_IDS),
        )
    print("Готово: первый пользователь создан; пароль нигде не выведен", flush=True)


if __name__ == "__main__":
    main()
