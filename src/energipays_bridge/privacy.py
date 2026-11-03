"""Keep personal data out of logs and stored points (#7).

Logs and the points table are what people paste into issues, so neither carries the
account's address or phone, and log lines carry the email masked.
"""
from __future__ import annotations

try:  # energipays-client >= 0.3.1
    from energipays import mask_email
except ImportError:  # pragma: no cover - older pin
    def mask_email(email: str | None) -> str:
        if not email or "@" not in email:
            return "•••" if email else ""
        local, _, domain = email.partition("@")
        shown = local[0] + "•••" + local[-1] if len(local) > 2 else local[:1] + "•••"
        return f"{shown}@{domain}"

__all__ = ["mask_email"]
