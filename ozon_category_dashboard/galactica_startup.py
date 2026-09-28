"""Opt-in Windows source startup. DPAPI CurrentUser, no plaintext key files."""

import base64
import ctypes
import hashlib
import json
import os
import stat
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from galactica_authorization_codes import SourceCodeTarget
from galactica_consent import ConsentConfiguration
from galactica_exchange_http import ExchangeConfiguration, _object

ENTROPY = b"PULSE-GALACTICA-source-startup-v1"
LIMIT = 16384


class SourceStartupError(RuntimeError):
    pass


def _dpapi(value: bytes, *, protect: bool) -> bytes:
    """Authenticated OS protection for this Windows user; UI is forbidden.

    No LOCAL_MACHINE scope, exported recovery secret, impersonation or fallback.
    Same-user administrator/operator compromise is outside this storage boundary.
    """
    if os.name != "nt" or type(value) is not bytes or not 0 < len(value) <= LIMIT:
        raise SourceStartupError("Source configuration protection unavailable")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    input_buffer = ctypes.create_string_buffer(value)
    entropy_buffer = ctypes.create_string_buffer(ENTROPY)
    incoming = Blob(len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    try:
        if not function(
            ctypes.byref(incoming),
            None,
            ctypes.byref(entropy),
            None,
            None,
            1,
            ctypes.byref(outgoing),
        ):  # CRYPTPROTECT_UI_FORBIDDEN
            raise SourceStartupError("Source configuration protection unavailable")
        if not 0 < outgoing.size <= LIMIT:
            raise SourceStartupError("Source configuration protection unavailable")
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        if outgoing.data:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel.LocalFree(outgoing.data)


def seal_configuration(document: bytes) -> bytes:
    """Operator API, not HTTP. Caller persists returned ciphertext, never document.

    This does not install/activate anything or generate shared exchange credentials.
    It intentionally cannot make malformed JSON valid: startup still validates it.
    """
    return _dpapi(document, protect=True)


def install_from_environment(app) -> bool:
    path = os.environ.get("PULSE_GALACTICA_STARTUP_FILE", "")
    if not path:
        return False
    try:
        if (
            os.name != "nt"
            or not Path(path).is_absolute()
            or getattr(app, "GALACTICA_CONSENT_CONFIG", None) is not None
            or getattr(app, "GALACTICA_EXCHANGE_CONFIG", None) is not None
        ):
            raise ValueError("Invalid startup state")
        # Ciphertext may be backed up; its authenticated plaintext is never logged.
        # Reject reparse-point paths; DPAPI integrity also prevents injected plaintext.
        target = Path(path)
        for part in (target, *target.parents):
            info = part.lstat()
            if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Reparse path")
        with target.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("Invalid protected file")
            ciphertext = stream.read(LIMIT + 1)
            after = os.fstat(stream.fileno())
            if (
                len(ciphertext) > LIMIT
                or before.st_size != len(ciphertext)
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                raise ValueError("Changed protected file")
        data = json.loads(_dpapi(ciphertext, protect=False), object_pairs_hook=_object)
        fields = {
            "version",
            "origin",
            "instance_id",
            "client_id",
            "redirect_uri",
            "consent_key",
            "exchange_sha256",
        }
        if (
            type(data) is not dict
            or set(data) != fields
            or type(data["version"]) is not int
            or data["version"] != 1
            or any(type(data[key]) is not str for key in fields - {"version"})
        ):
            raise ValueError("Invalid source configuration")
        instance = UUID(data["instance_id"])
        if str(instance) != data["instance_id"] or data["instance_id"] != os.environ.get(
            "PULSE_GALACTICA_INSTANCE_ID"
        ):
            raise ValueError("Source instance mismatch")
        target_config = SourceCodeTarget(instance, data["client_id"], data["redirect_uri"])
        origin = urlsplit(data["origin"])
        callback = urlsplit(data["redirect_uri"])
        if (
            callback.path != "/source/callback"
            or origin.port == 0
            or callback.port == 0
            or len(data["origin"]) > 512
        ):
            raise ValueError("Invalid fixed origin")
        key = base64.urlsafe_b64decode(data["consent_key"])
        if (
            len(key) != 32
            or base64.urlsafe_b64encode(key).decode() != data["consent_key"]
            or not (
                os.environ.get("DASHBOARD_USER_SESSION_SECRET")
                or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
            )
            or key == app._managed_access_signing_key()
            or hashlib.sha256(data["consent_key"].rstrip("=").encode()).hexdigest()
            == data["exchange_sha256"]
        ):
            raise ValueError("Invalid key separation")
        consent = ConsentConfiguration(target_config, data["origin"], key)
        exchange = ExchangeConfiguration(target_config, origin.netloc, data["exchange_sha256"])
        # Both configs fully validated before first publication; run before server bind.
        app.GALACTICA_CONSENT_CONFIG = consent
        app.GALACTICA_EXCHANGE_CONFIG = exchange
        return True
    except Exception:
        raise SourceStartupError("PULSE source startup configuration unavailable") from None
