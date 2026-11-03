"""One reading of what an energipays-client call returned (#8).

The client never raises on an HTTP error; it returns a dict. Every caller has to tell
apart an error, a skipped write, a queued command and a success, and before this module
several callers reported every one of them as "OK".
"""
from __future__ import annotations


def outcome(result: object) -> tuple[str, str]:
    """Return ``(kind, detail)`` where kind is ``ok``, ``queued``, ``skipped`` or ``error``."""
    if isinstance(result, dict):
        if "error" in result or "error_decrypted" in result:
            detail = (result.get("error_decrypted") or result.get("body")
                      or result.get("error") or "cloud error")
            return "error", str(detail)[:300]
        if result.get("skipped"):
            return "skipped", str(result["skipped"])
        if result.get("queued"):
            return "queued", "queued for the device"
    return "ok", ""


def label(kind: str, detail: str) -> str:
    """Short status for logs and the MQTT Last Command Result sensor."""
    return {"ok": "OK", "queued": "QUEUED", "skipped": f"SKIPPED ({detail})",
            "error": f"FAILED ({detail})"}[kind]
