"""Tell users what happened to their account and folders while they were away.

Admin actions (role changes, group membership, grants, deletions), shares being
revoked or imported, and the retention sweep all change what a user can read
without that user doing anything. Each one leaves an ``account_event`` row in
the existing ``notifications`` inbox, which the web client shows as a log.

``notify`` only adds rows; the caller's commit persists them with the change
they describe, so a rolled-back action leaves no event behind.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SearchSpace, User, UserGroupMembership
from app.notifications.constants import TITLE_MAX_LENGTH
from app.notifications.persistence import Notification

ACCOUNT_EVENT = "account_event"


def notify(
    session: AsyncSession,
    user_ids: Iterable[Any],
    title: str,
    message: str,
    kind: str,
) -> None:
    """Queue one event per distinct user; ``None`` ids are skipped."""
    for user_id in {u for u in user_ids if u is not None}:
        session.add(
            Notification(
                user_id=user_id,
                type=ACCOUNT_EVENT,
                title=title[:TITLE_MAX_LENGTH],
                message=message,
                notification_metadata={"kind": kind},
            )
        )


async def space_owner_ids(session: AsyncSession, space_ids: Iterable[int]) -> list[Any]:
    ids = list({s for s in space_ids if s is not None})
    if not ids:
        return []
    return list(
        (await session.execute(select(SearchSpace.user_id).where(SearchSpace.id.in_(ids))))
        .scalars()
        .all()
    )


async def group_member_ids(session: AsyncSession, group_id: int) -> list[Any]:
    return list(
        (
            await session.execute(
                select(UserGroupMembership.user_id).where(
                    UserGroupMembership.group_id == group_id
                )
            )
        )
        .scalars()
        .all()
    )


async def admin_ids(session: AsyncSession, *, except_id: Any = None) -> list[Any]:
    rows = (
        await session.execute(
            select(User.id).where(User.is_superuser.is_(True), User.is_active.is_(True))
        )
    ).scalars()
    return [u for u in rows if u != except_id]


async def notify_readers_of_deleted_folder(
    session: AsyncSession, folder_ids: list[int], name: str
) -> None:
    """Tell importers and group members that a folder they read is being deleted.

    ``folder_ids`` is the deleted subtree: a share or grant may sit on any
    folder in it, not just the root.
    """
    from app.db import FolderGroupGrant, FolderLink, SharedFolder
    from app.services.folder_sharing_service import share_is_live

    target_spaces = (
        await session.execute(
            select(FolderLink.target_search_space_id)
            .join(SharedFolder, SharedFolder.id == FolderLink.share_id)
            .where(FolderLink.source_folder_id.in_(folder_ids), share_is_live())
        )
    ).scalars().all()
    members = (
        await session.execute(
            select(UserGroupMembership.user_id)
            .join(
                FolderGroupGrant,
                FolderGroupGrant.group_id == UserGroupMembership.group_id,
            )
            .where(FolderGroupGrant.folder_id.in_(folder_ids))
        )
    ).scalars().all()
    notify(
        session,
        [*await space_owner_ids(session, target_spaces), *members],
        f'Folder "{name}" was deleted',
        f'"{name}", which you could read through a share or a user group, was '
        "deleted by its owner and no longer answers your questions.",
        "shared_folder_deleted",
    )
