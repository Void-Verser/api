"""API-key ORM model.

Only the hash is persisted; plaintext is shown once at creation time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Individual scopes. "*" grants every current and future client scope and is
# the default for new keys: one key per bot covers the whole client API.
SCOPES = {"search", "info", "stream", "download", "resolve", "convert"}
ALL_SCOPES = "*"
VALID_PERMISSIONS = SCOPES | {ALL_SCOPES}
DEFAULT_PERMISSIONS = [ALL_SCOPES]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("owners.id", ondelete="SET NULL"), nullable=True, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    key_last3: Mapped[str] = mapped_column(String(8), nullable=False, default="***")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # SQLAlchemy reserves Model.metadata, so the attribute is `meta`
    # while the column stays named "metadata".
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc)

    def has_permission(self, scope: str) -> bool:
        perms = self.permissions or []
        return ALL_SCOPES in perms or scope in perms

    @property
    def masked(self) -> str:
        return f"mapi_****************{self.key_last3}"
