"""Pydantic schemas for folder CRUD, move, and reorder operations."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FolderCreate(BaseModel):
    name: str = Field(max_length=255, min_length=1)
    parent_id: int | None = None
    search_space_id: int


class FolderUpdate(BaseModel):
    name: str = Field(max_length=255, min_length=1)


class FolderMove(BaseModel):
    new_parent_id: int | None = None


class FolderReorder(BaseModel):
    before_position: str | None = None
    after_position: str | None = None


class FolderScopeUpdate(BaseModel):
    """Target scope for a folder subtree; only promotion is supported."""

    scope: Literal["space"]


class FolderRead(BaseModel):
    id: int
    name: str
    position: str
    parent_id: int | None
    search_space_id: int
    created_by_id: UUID | None
    # NULL = space-wide; a thread id = visible only in that chat session.
    owner_thread_id: int | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias="folder_metadata"
    )

    model_config = ConfigDict(from_attributes=True)


class FolderBreadcrumb(BaseModel):
    id: int
    name: str


class DocumentMove(BaseModel):
    folder_id: int | None = None


class BulkDocumentMove(BaseModel):
    document_ids: list[int]
    folder_id: int | None = None
