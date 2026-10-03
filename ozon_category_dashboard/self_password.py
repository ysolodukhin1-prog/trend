"""Personal browser password change; no administrator or delegated identity fallback."""
import base64
import hmac
import json
import logging
import secrets

from galactica_entitlement import (SourceDenied, require_access_origin,
    require_current_session, transaction_limits, verified_subject)
from user_registry import password_digest

log = logging.getLogger(__name__)


class WrongPassword(ValueError):
    pass


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {'current_password', 'new_password', 'confirm_password'}:
        raise ValueError('Некорректные поля формы.')
    old, new, confirm = (payload[k] for k in ('current_password', 'new_password', 'confirm_password'))
    if any(not isinstance(v, str) or not v or len(v) > 1024 for v in (old, new, confirm)):
        raise ValueError('Заполните все поля. Пароль — не более 1024 символов.')
    if len(new) < 10:
        raise ValueError('Новый пароль должен содержать не менее 10 символов.')
    if new != confirm:
        raise ValueError('Новые пароли не совпадают.')
    if new == old:
        raise ValueError('Новый пароль должен отличаться от текущего.')
    return old, new


def change_password(connection, subject, old, new):
    """Caller owns transaction. Lock user before sessions, matching session renewal order."""
    transaction_limits(connection)
    with connection.cursor() as cursor:
        cursor.execute('SELECT password_salt,password_hash FROM public.bi_users WHERE user_id=%s FOR UPDATE', (subject[0],))
        row = cursor.fetchone()
        require_current_session(connection, subject)
        if row is None:
            raise SourceDenied()
        salt, digest = (row['password_salt'], row['password_hash']) if isinstance(row, dict) else row
        if not hmac.compare_digest(password_digest(old, salt), digest):
            raise WrongPassword('Текущий пароль неверен.')
        salt = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip('=')
        cursor.execute('UPDATE public.bi_users SET password_salt=%s,password_hash=%s,updated_at=clock_timestamp() WHERE user_id=%s',
                       (salt, password_digest(new, salt), subject[0]))
        cursor.execute('UPDATE public.bi_personal_sessions SET revoked_at=clock_timestamp() WHERE user_id=%s AND revoked_at IS NULL', (subject[0],))


def handle(app, handler):
    subject = None
    headers = {'Cache-Control': 'no-store, max-age=0'}
    def reply(status, message):
        log.info('self_password user_id=%s status=%s', subject[0] if subject else None, status)
        handler.send_json({'ok': False, 'error': message}, status=status, headers=headers)
    try:
        require_access_origin(handler.headers)
    except SourceDenied:
        return reply(403, 'Недопустимый источник запроса.')
    try:
        token = app.dashboard_access_session_from_cookie(handler.headers.get('Cookie'))
        subject = verified_subject(token, app._managed_access_signing_key())
    except SourceDenied:
        return reply(401, 'Войдите в свою учётную запись TREND.')
    rate_key = 'self-password:' + str(subject[0])
    if app.admin_login_retry_after(rate_key):
        return reply(429, 'Слишком много попыток. Повторите позже.')
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 16000 or handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return reply(400, 'Некорректный запрос.')
        # Do not pass password fields through generic request-body audit capture.
        payload = json.loads(handler.rfile.read(length).decode('utf-8'))
        old, new = validate(payload)
        with app.client_registry_connection() as connection:
            change_password(connection, subject, old, new)
    except WrongPassword:
        app.record_admin_login_failure(rate_key)
        return reply(400, 'Текущий пароль неверен.')
    except SourceDenied:
        return reply(401, 'Сессия завершена. Войдите в TREND заново.')
    except (ValueError, UnicodeError) as exc:
        return reply(400, str(exc) if type(exc) is ValueError else 'Некорректный запрос.')
    except Exception:
        # Never log request payloads, SQL arguments or credentials.
        return reply(503, 'Не удалось сменить пароль. Повторите позже.')
    app.clear_admin_login_failures(rate_key)
    headers['Set-Cookie'] = handler.dashboard_access_cookie_header(clear=True)
    log.info('self_password user_id=%s status=200 sessions_revoked=true', subject[0])
    handler.send_json({'ok': True, 'reauthenticate': True}, headers=headers)
