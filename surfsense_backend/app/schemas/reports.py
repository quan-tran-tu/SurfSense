"""Report schemas for API responses."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    """Ask the server to write a report, without going through the chat agent.

    Callers that already know what they want — a ``/report`` command, say —
    supply these values directly instead of paying for an agent turn to guess
    them. Only ``topic`` and ``search_queries`` are derived by a model, and
    only when omitted.
    """

    search_space_id: int
    request: str = Field(
        min_length=1,
        description="What the report should cover, in the user's own words.",
    )
    thread_id: int | None = Field(
        default=None,
        description=(
            "Chat thread to attribute the report to, so it shows up in that "
            "session's report list. Optional."
        ),
    )
    report_style: Literal["detailed", "brief", "deep_research"] = "detailed"
    user_instructions: str | None = Field(
        default=None,
        description=(
            "Extra formatting or focus instructions passed straight to the "
            "report writer — e.g. a template's outline and style excerpt."
        ),
    )
    parent_report_id: int | None = Field(
        default=None,
        description="Revise this report instead of writing a new one.",
    )
    topic: str | None = Field(
        default=None,
        description="Report title. Derived from `request` when omitted.",
    )
    search_queries: list[str] | None = Field(
        default=None,
        max_length=5,
        description=(
            "Knowledge-base queries to gather source material. Derived from "
            "`request` when omitted. Also run for revisions, so that "
            "'add a section about X' can pull in material the original "
            "report never covered."
        ),
    )


class ReportGenerateResponse(BaseModel):
    """Outcome of a generate call.

    Returned with HTTP 200 even when ``status`` is ``failed``: the pipeline
    persists a failed report row either way, and the caller wants its id.
    """

    status: Literal["ready", "failed"]
    report_id: int | None = None
    title: str
    word_count: int = 0
    is_revision: bool = False
    message: str | None = None
    error: str | None = None


class ReportBase(BaseModel):
    """Base report schema."""

    title: str
    content: str | None = None
    report_style: str | None = None
    search_space_id: int


class ReportRead(BaseModel):
    """Schema for reading a report (list view, no content)."""

    id: int
    title: str
    report_style: str | None = None
    report_metadata: dict[str, Any] | None = None
    report_group_id: int | None = None
    content_type: str = "markdown"
    thread_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReportVersionInfo(BaseModel):
    """Lightweight version entry for the version switcher UI."""

    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ReportContentRead(BaseModel):
    """Schema for reading a report with full content (Markdown or Typst)."""

    id: int
    title: str
    content: str | None = None
    content_type: str = "markdown"
    report_metadata: dict[str, Any] | None = None
    report_group_id: int | None = None
    versions: list[ReportVersionInfo] = []

    class Config:
        from_attributes = True


class ReportContentUpdate(BaseModel):
    """Schema for updating a report's Markdown content."""

    content: str
