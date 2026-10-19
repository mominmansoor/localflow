"""Encrypt secret snippets (passwords etc.) at rest.

On Windows this uses DPAPI (CryptProtectData), so config.json only ever holds
ciphertext that just your Windows account on this PC can decrypt. Elsewhere
(tests / other OSes) it falls back to base64 obfuscation and says so.
"""
from __future__ import annotations

import base64
import sys

PREFIX_DPAPI = "dpapi:"
PREFIX_B64 = "b64:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    crypt = ctypes.windll.crypt32
    UI_FORBIDDEN = 0x1
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(blob_in), "LocalFlow", None, None, None,
                                    UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                      UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(plain: str) -> str:
    raw = plain.encode("utf-8")
    if sys.platform == "win32":
        return PREFIX_DPAPI + base64.b64encode(_dpapi(raw, True)).decode()
    return PREFIX_B64 + base64.b64encode(raw).decode()


def unprotect(stored: str) -> str:
    if stored.startswith(PREFIX_DPAPI):
        return _dpapi(base64.b64decode(stored[len(PREFIX_DPAPI):]), False).decode("utf-8")
    if stored.startswith(PREFIX_B64):
        return base64.b64decode(stored[len(PREFIX_B64):]).decode("utf-8")
    return stored  # plain (not yet encrypted)


def snippet_text(snip: dict) -> str:
    t = snip.get("text", "")
    if snip.get("secret"):
        try:
            return unprotect(t)
        except Exception:
            return ""
    return t
