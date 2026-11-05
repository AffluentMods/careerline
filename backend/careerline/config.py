# SPDX-License-Identifier: Apache-2.0
"""Application settings.

Every setting is read from an environment variable prefixed with ``CAREERLINE_``.
Any secret may instead be read from a file by setting ``CAREERLINE_<NAME>_FILE``
(useful with Docker secrets). Secrets are never given defaults.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PREFIX = "CAREERLINE_"
_FILE_BACKED = (
    "DATABASE_URL",
    "ENCRYPTION_KEYS",
    "SMTP_PASSWORD",
    "BACKUP_KEY",
    "OIDC_CLIENT_SECRET",
)


def _load_file_backed_env() -> None:
    for name in _FILE_BACKED:
        file_var = f"{_PREFIX}{name}_FILE"
        path = os.environ.get(file_var)
        if path and not os.environ.get(f"{_PREFIX}{name}"):
            os.environ[f"{_PREFIX}{name}"] = Path(path).read_text(encoding="utf-8").strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=_PREFIX, env_file=".env", extra="ignore")

    env: Literal["production", "development", "test"] = "production"

    # postgresql://user:pass@host:5432/db  (the async driver is selected automatically)
    database_url: SecretStr
    # Comma-separated "version:base64url-32-byte-key" pairs. The FIRST entry encrypts;
    # all entries can decrypt, which allows key rotation.
    encryption_keys: SecretStr

    allow_registration: bool = False
    cookie_secure: bool = True
    hsts: bool = False

    session_ttl_hours: int = 24 * 7
    session_idle_minutes: int = 60 * 12
    mfa_pending_minutes: int = 5

    max_body_bytes: int = 1_000_000
    # Resume uploads only. Everything else keeps max_body_bytes.
    import_max_bytes: int = 5_000_000
    # Tracker attachments: per file, and total per user.
    attachment_max_bytes: int = 10_000_000
    attachment_quota_bytes: int = 250_000_000
    # Let outbound requests (webhooks, ntfy, feeds) reach private/LAN addresses.
    # Off by default: it would let a configured URL probe your internal network.
    outbound_allow_private: bool = False
    static_dir: Path | None = None
    # Where CareerLine is reached from outside, e.g. https://careerline.example.com. Used for
    # links in notifications and the default OIDC redirect address.
    public_url: str = ""

    # --- notifications by email (one SMTP server for the whole instance) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""

    # --- data export, import and backups ---
    # Largest archive accepted by "Import from a CareerLine export".
    import_archive_max_bytes: int = 300_000_000
    # Scheduled encrypted backups of the whole database. Off when no directory is set.
    backup_dir: Path | None = None
    # base64url 32-byte key. When empty, a key derived from the first encryption key is used.
    backup_key: SecretStr | None = None
    backup_interval_hours: int = 24
    backup_keep: int = 14

    # --- single sign-on (OpenID Connect, e.g. Authelia, Authentik, Keycloak) ---
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr | None = None
    # Defaults to <public_url>/api/v1/auth/oidc/callback.
    oidc_redirect_url: str = ""
    oidc_label: str = "single sign-on"
    oidc_scopes: str = "openid email profile"
    # Create an account the first time someone signs in through the identity provider,
    # even when registration is closed.
    oidc_auto_create: bool = False
    # Sign an existing account in when the provider vouches for the same (verified) email.
    oidc_link_by_email: bool = False
    # Turn off email + password sign-in (single sign-on only).
    password_login: bool = True

    # Demo mode: fictional seed data, registration closed, nothing leaves the server.
    demo_mode: bool = False

    @field_validator("static_dir", "backup_dir", mode="before")
    @classmethod
    def _empty_path(cls, v: object) -> object:
        return None if v in ("", None) else v

    @field_validator("smtp_password", "backup_key", "oidc_client_secret", mode="before")
    @classmethod
    def _empty_secret(cls, v: object) -> object:
        return None if v in ("", None) else v

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if not self.password_login and not self.oidc_enabled:
            raise ValueError(
                "CAREERLINE_PASSWORD_LOGIN=false needs single sign-on (CAREERLINE_OIDC_*)"
            )
        if self.oidc_issuer and not self.oidc_callback_url:
            raise ValueError(
                "Set CAREERLINE_OIDC_REDIRECT_URL or CAREERLINE_PUBLIC_URL for single sign-on"
            )
        if self.backup_interval_hours < 1 or self.backup_keep < 1:
            raise ValueError("Backup interval and retention must be at least 1")
        return self

    @property
    def oidc_callback_url(self) -> str:
        if self.oidc_redirect_url:
            return self.oidc_redirect_url
        return f"{self.public_url.rstrip('/')}/api/v1/auth/oidc/callback" if self.public_url else ""

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_callback_url)

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def async_database_url(self) -> str:
        url = self.database_url.get_secret_value()
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix) :]
        raise ValueError("CAREERLINE_DATABASE_URL must be a postgresql:// URL")

    @property
    def session_cookie(self) -> str:
        # The __Host- prefix makes browsers enforce Secure, Path=/ and no Domain attribute.
        return "__Host-cl_session" if self.cookie_secure else "cl_session"

    @property
    def csrf_cookie(self) -> str:
        return "__Host-cl_csrf" if self.cookie_secure else "cl_csrf"


@lru_cache
def get_settings() -> Settings:
    _load_file_backed_env()
    return Settings()  # type: ignore[call-arg, unused-ignore]
