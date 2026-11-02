"""Runtime settings. Everything comes from the environment; secrets are never defaulted."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .identity import check_pepper

MIN_ADMIN_KEY_CHARS = 32


@dataclass(frozen=True)
class Settings:
    db_path: str
    pepper: bytes            # keys the UCID -> pilot id HMAC; rotating it orphans every account
    admin_key: str           # X-Admin-Key for server and fleet registration
    presence_ttl: int = 300  # seconds since last report before a server counts as offline
    greet_gap: int = 1800    # seconds before the same pilot is welcomed again on the same server
    request_ttl: int = 7 * 86400  # pending friend requests expire after this
    max_players_per_report: int = 1000
    trust_proxy: bool = False  # read CF-Connecting-IP (set only behind the Cloudflare tunnel)
    docs: bool = False       # expose /docs and /openapi.json (off on the public listener)
    project_url: str = "github.com/Fox3Luck/DCS-Wingman"

    def validate(self) -> None:
        check_pepper(self.pepper)
        if self.pepper != self.pepper.strip():
            raise RuntimeError("WINGMAN_PEPPER has leading or trailing whitespace; strip it")
        if len(self.admin_key) < MIN_ADMIN_KEY_CHARS or self.admin_key != self.admin_key.strip():
            raise RuntimeError(f"WINGMAN_ADMIN_KEY must be at least {MIN_ADMIN_KEY_CHARS} characters, no surrounding whitespace")
        for name in ("presence_ttl", "greet_gap", "request_ttl", "max_players_per_report"):
            if getattr(self, name) <= 0:
                raise RuntimeError(f"{name} must be positive")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def load_settings() -> Settings:
    st = Settings(
        db_path=os.environ.get("WINGMAN_DB", "wingman.db").strip() or "wingman.db",
        pepper=os.environ.get("WINGMAN_PEPPER", "").strip().encode("utf-8"),
        admin_key=os.environ.get("WINGMAN_ADMIN_KEY", "").strip(),
        presence_ttl=_env_int("WINGMAN_PRESENCE_TTL", 300),
        greet_gap=_env_int("WINGMAN_GREET_GAP", 1800),
        request_ttl=_env_int("WINGMAN_REQUEST_TTL", 7 * 86400),
        trust_proxy=os.environ.get("WINGMAN_TRUST_PROXY", "0").strip() == "1",
        docs=os.environ.get("WINGMAN_DOCS", "0").strip() == "1",
    )
    st.validate()
    return st
