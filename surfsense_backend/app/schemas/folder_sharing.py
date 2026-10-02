"""Pydantic schemas for cross-user folder sharing (`/share` and `/import`)."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ShareState = Literal["live", "revoked", "expired"]


class FolderShareCreate(BaseModel):
    """Body of ``POST /search-spaces/{id}/folder-shares``."""

    path: str = Field(min_length=1, description='Folder path, e.g. "Research/AI"')
    name: str | None = Field(default=None, max_length=100)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)

    @field_validator("expires_at")
    @classmethod
    def _in_future(cls, value: datetime | None) -> datetime | None:
        # A share that is born expired grants nothing; refuse it rather than
        # mint a token that only looks usable.
        if value is not None and value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future (omit it for no expiry)")
        return value


class FolderShareRead(BaseModel):
    id: int
    token: str
    source_folder_id: int
    source_search_space_id: int
    created_by_id: UUID | None
    created_at: datetime
    expires_at: datetime | None
    max_uses: int | None
    uses_count: int
    revoked_at: datetime | None
    name: str | None
    # Filled by the listing; the folder's name, and whether the token still works.
    folder_name: str | None = None
    state: ShareState = "live"

    model_config = ConfigDict(from_attributes=True)


class FolderLinkCreate(BaseModel):
    """Body of ``POST /search-spaces/{id}/folder-links``."""

    token: str = Field(min_length=1)


class FolderLinkRead(BaseModel):
    id: int
    share_id: int
    source_folder_id: int
    target_search_space_id: int
    created_by_id: UUID | None
    created_at: datetime
    # Denormalized for the CLI, which prints it on /import.
    folder_name: str
    # Whether the underlying share still resolves. A link whose share was revoked
    # or has expired stays in the table but stops returning documents on the next
    # query (see folder_sharing_service.linked_folder_ids), so a client that only
    # listed links would show a dead import as a live one. Always true at creation.
    live: bool = True
    # How it ended, when it did: the owner revoked it, or its expiry passed.
    state: ShareState = "live"

    model_config = ConfigDict(from_attributes=True)
