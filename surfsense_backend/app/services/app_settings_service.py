"""Deployment-wide settings an admin edits from the UI, over env defaults.

One row per setting in ``app_settings``. While a row exists it wins; deleting it
falls back to the env value the deployment set. Only folder retention lives here
today.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import config
from app.db import AppSetting

FOLDER_RETENTION_KEY = "folder_retention_days"

# Folder kinds, in the order the retention task classifies a folder: the first
# that applies wins. See ``folder_retention_task.folder_kind``.
RETENTION_KINDS = ("session", "group", "admin", "space")


def env_retention_days() -> dict[str, int | None]:
    """The deployment's defaults, from ``*_FOLDER_RETENTION_DAYS``."""
    return {
        "session": config.SESSION_FOLDER_RETENTION_DAYS,
        "group": config.GROUP_FOLDER_RETENTION_DAYS,
        "admin": config.ADMIN_FOLDER_RETENTION_DAYS,
        "space": config.SPACE_FOLDER_RETENTION_DAYS,
    }


def _clean(value: Any) -> int | None:
    """A stored day count; anything not a positive whole number keeps forever."""
    return value if isinstance(value, int) and value > 0 else None


async def get_retention_override(session: AsyncSession) -> dict[str, int | None] | None:
    """The admin's retention settings, or ``None`` when they never set any."""
    row = await session.get(AppSetting, FOLDER_RETENTION_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    return {kind: _clean(row.value.get(kind)) for kind in RETENTION_KINDS}


async def effective_retention_days(session: AsyncSession) -> dict[str, int | None]:
    """What the retention task applies: the admin's settings, else the env's."""
    return await get_retention_override(session) or env_retention_days()


async def set_retention_override(
    session: AsyncSession, days: dict[str, int | None], user_id: Any
) -> None:
    value = {kind: _clean(days.get(kind)) for kind in RETENTION_KINDS}
    row = await session.get(AppSetting, FOLDER_RETENTION_KEY)
    if row is None:
        session.add(AppSetting(key=FOLDER_RETENTION_KEY, value=value, updated_by_id=user_id))
    else:
        row.value = value
        row.updated_by_id = user_id
    await session.commit()


async def clear_retention_override(session: AsyncSession) -> None:
    row = await session.get(AppSetting, FOLDER_RETENTION_KEY)
    if row is not None:
        await session.delete(row)
        await session.commit()
