"""Managed BI users and client/report access grants."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re


USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ITERATIONS = 310_000


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    clean = str(value or "")
    return base64.urlsafe_b64decode(clean + "=" * (-len(clean) % 4))


def password_digest(password: str, salt: str) -> str:
    return _b64(
        hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            _decode(salt),
            PASSWORD_ITERATIONS,
        )
    )


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_users (
                user_id bigserial PRIMARY KEY,
                username text NOT NULL UNIQUE,
                display_name text NOT NULL,
                email text NOT NULL DEFAULT '',
                email_verified boolean NOT NULL DEFAULT false,
                password_salt text NOT NULL,
                password_hash text NOT NULL,
                is_active boolean NOT NULL DEFAULT true,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("ALTER TABLE public.bi_users ADD COLUMN IF NOT EXISTS email text NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE public.bi_users ADD COLUMN IF NOT EXISTS email_verified boolean NOT NULL DEFAULT false")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_user_clients (
                user_id bigint NOT NULL REFERENCES public.bi_users(user_id) ON DELETE CASCADE,
                client_key text NOT NULL,
                PRIMARY KEY (user_id, client_key)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_user_reports (
                user_id bigint NOT NULL REFERENCES public.bi_users(user_id) ON DELETE CASCADE,
                report_id text NOT NULL,
                PRIMARY KEY (user_id, report_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_user_admin_sections (
                user_id bigint NOT NULL REFERENCES public.bi_users(user_id) ON DELETE CASCADE,
                section_key text NOT NULL,
                PRIMARY KEY (user_id, section_key)
            )
            """
        )
        cur.execute("ALTER TABLE public.bi_users ADD COLUMN IF NOT EXISTS data_access jsonb NOT NULL DEFAULT '[]'::jsonb")
    conn.commit()


def _serialize(row) -> dict:
    item = dict(row)
    for field in ("created_at", "updated_at"):
        value = item.get(field)
        item[field] = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
    item["data_access"] = list(item.get("data_access") or [])
    item["clients"] = list(item.get("clients") or [])
    item["reports"] = list(item.get("reports") or [])
    item["admin_sections"] = list(item.get("admin_sections") or [])
    item.pop("password_salt", None)
    item.pop("password_hash", None)
    return item


def list_users(conn, *, prepare_schema: bool = True) -> list[dict]:
    if prepare_schema:
        ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.user_id, u.username, u.display_name, u.email, u.email_verified, u.is_active,
                   u.created_at, u.updated_at, u.data_access,
                   COALESCE((SELECT jsonb_agg(c.client_key ORDER BY c.client_key)
                             FROM public.bi_user_clients c WHERE c.user_id = u.user_id), '[]'::jsonb) AS clients,
                   COALESCE((SELECT jsonb_agg(r.report_id ORDER BY r.report_id)
                             FROM public.bi_user_reports r WHERE r.user_id = u.user_id), '[]'::jsonb) AS reports,
                   COALESCE((SELECT jsonb_agg(a.section_key ORDER BY a.section_key)
                             FROM public.bi_user_admin_sections a WHERE a.user_id = u.user_id), '[]'::jsonb) AS admin_sections
            FROM public.bi_users u
            ORDER BY lower(u.display_name), u.username
            """
        )
        rows = cur.fetchall()
    return [_serialize(row) for row in rows]


def active_user_count(conn) -> int:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.bi_users WHERE is_active")
        row = cur.fetchone()
    return int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)


def save_user(
    conn,
    payload: dict,
    allowed_clients: set[str],
    allowed_reports: set[str],
    allowed_admin_sections: set[str] | None = None,
    allowed_data_sources: list | None = None,
) -> dict:
    ensure_schema(conn)
    allowed_admin_sections = allowed_admin_sections or set()
    username = str(payload.get("username") or "").strip().lower()
    display_name = str(payload.get("display_name") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    email_verified = bool(payload.get("email_verified", False)) if email else False
    password = str(payload.get("password") or "")
    clients = sorted({str(value).strip().lower() for value in payload.get("clients", []) if str(value).strip()})
    reports = sorted({str(value).strip() for value in payload.get("reports", []) if str(value).strip()})
    admin_sections = sorted({
        str(value).strip()
        for value in payload.get("admin_sections", [])
        if str(value).strip()
    })
    from data_access import normalize_grants
    data_access = normalize_grants(payload.get('data_access', []), allowed_data_sources or []) if 'data_access' in payload else None
    is_active = bool(payload.get("is_active", True))
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Логин: 3–64 латинских символа, цифры, точка, дефис или _")
    if not display_name or len(display_name) > 120:
        raise ValueError("Укажите имя пользователя длиной до 120 символов")
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError("Укажите корректную почту пользователя")
    if set(clients) - allowed_clients:
        raise ValueError("Выбраны неизвестные клиенты")
    if set(reports) - allowed_reports:
        raise ValueError("Выбраны неизвестные отчёты")
    if set(admin_sections) - allowed_admin_sections:
        raise ValueError("Выбраны неизвестные разделы админки")
    if is_active and not clients:
        raise ValueError("Выберите хотя бы одного клиента")
    if is_active and not reports:
        raise ValueError("Выберите хотя бы один отчёт")
    user_id = int(payload.get("user_id") or 0)
    with conn.cursor() as cur:
        if user_id:
            cur.execute("SELECT user_id FROM public.bi_users WHERE user_id = %s", (user_id,))
            if not cur.fetchone():
                raise ValueError("Пользователь не найден")
            if password:
                salt = _b64(os.urandom(18))
                digest = password_digest(password, salt)
                cur.execute(
                    """UPDATE public.bi_users SET username=%s, display_name=%s, email=%s, email_verified=%s, is_active=%s,
                       password_salt=%s, password_hash=%s, updated_at=now() WHERE user_id=%s""",
                    (username, display_name, email, email_verified, is_active, salt, digest, user_id),
                )
            else:
                cur.execute(
                    "UPDATE public.bi_users SET username=%s, display_name=%s, email=%s, email_verified=%s, is_active=%s, updated_at=now() WHERE user_id=%s",
                    (username, display_name, email, email_verified, is_active, user_id),
                )
        else:
            if len(password) < 10:
                raise ValueError("Для нового пользователя задайте пароль не короче 10 символов")
            salt = _b64(os.urandom(18))
            digest = password_digest(password, salt)
            cur.execute(
                """INSERT INTO public.bi_users (username, display_name, email, email_verified, password_salt, password_hash, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING user_id""",
                (username, display_name, email, email_verified, salt, digest, is_active),
            )
            row = cur.fetchone()
            user_id = int(row[0] if not isinstance(row, dict) else next(iter(row.values())))
        if data_access is not None:
            from psycopg2.extras import Json
            cur.execute("UPDATE public.bi_users SET data_access=%s WHERE user_id=%s", (Json(data_access), user_id))
        cur.execute("DELETE FROM public.bi_user_clients WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM public.bi_user_reports WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM public.bi_user_admin_sections WHERE user_id=%s", (user_id,))
        if clients:
            cur.executemany(
                "INSERT INTO public.bi_user_clients (user_id, client_key) VALUES (%s, %s)",
                [(user_id, value) for value in clients],
            )
        if reports:
            cur.executemany(
                "INSERT INTO public.bi_user_reports (user_id, report_id) VALUES (%s, %s)",
                [(user_id, value) for value in reports],
            )
        if admin_sections:
            cur.executemany(
                "INSERT INTO public.bi_user_admin_sections (user_id, section_key) VALUES (%s, %s)",
                [(user_id, value) for value in admin_sections],
            )
    conn.commit()
    return next(item for item in list_users(conn) if int(item["user_id"]) == user_id)


def verify_user(conn, username: str, password: str) -> dict | None:
    ensure_schema(conn)
    normalized = str(username or "").strip().lower()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, username, display_name, password_salt, password_hash, is_active, updated_at FROM public.bi_users WHERE username=%s",
            (normalized,),
        )
        row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    supplied = password_digest(password, item["password_salt"])
    if not item.get("is_active") or not hmac.compare_digest(supplied, item["password_hash"]):
        return None
    from galactica_entitlement import session_revision

    # Do not bind an old password check to a newer revision read after a concurrent edit.
    user = next(
        (user for user in list_users(conn) if int(user["user_id"]) == int(item["user_id"])),
        None,
    )
    if not user or not user.get("is_active"):
        return None
    if session_revision(user.get("updated_at")) != session_revision(item.get("updated_at")):
        return None
    return user
