"""Pydantic schemas for cross-user folder sharing (`/share` and `/import`)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FolderShareCreate(BaseModel):
    """Body of ``POST /search-spaces/{id}/folder-shares``."""

    path: str = Field(min_length=1, description='Folder path, e.g. "Research/AI"')
    name: str | None = Field(default=None, max_length=100)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)


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

    model_config = ConfigDict(from_attributes=True)
