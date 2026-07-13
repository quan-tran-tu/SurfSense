"""API routes for cross-user folder sharing: mint a share token, accept it, revoke it.

Sharing is a *live link*, not a copy: accepting a token inserts a ``FolderLink``
and the importer's reads pass through to the sharer's rows. Nothing is duplicated,
so revoking the share removes the importer's access on their next query.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.context import AuthContext
from app.db import Folder, FolderLink, Permission, SharedFolder, get_async_session
from app.schemas.folder_sharing import (
    FolderLinkCreate,
    FolderLinkRead,
    FolderShareCreate,
    FolderShareRead,
)
from app.services.folder_service import resolve_folder_path
from app.services.folder_sharing_service import generate_share_token
from app.users import get_auth_context
from app.utils.rbac import check_permission

logger = logging.getLogger(__name__)

router = APIRouter()


def _link_read(link: FolderLink, folder_name: str) -> FolderLinkRead:
    return FolderLinkRead(
        id=link.id,
        share_id=link.share_id,
        source_folder_id=link.source_folder_id,
        target_search_space_id=link.target_search_space_id,
        created_by_id=link.created_by_id,
        created_at=link.created_at,
        folder_name=folder_name,
    )


@router.post(
    "/search-spaces/{search_space_id}/folder-shares", response_model=FolderShareRead
)
async def create_folder_share(
    search_space_id: int,
    request: FolderShareCreate,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Mint a share token for a folder subtree. Requires DOCUMENTS_READ."""
    await check_permission(
        session,
        auth,
        search_space_id,
        Permission.DOCUMENTS_READ.value,
        "You don't have permission to share folders from this search space",
    )

    folder = await resolve_folder_path(session, search_space_id, request.path)

    share = SharedFolder(
        token=generate_share_token(),
        source_folder_id=folder.id,
        source_search_space_id=search_space_id,
        created_by_id=auth.user.id,
        expires_at=request.expires_at,
        max_uses=request.max_uses,
        name=request.name or folder.name,
    )
    session.add(share)
    await session.commit()
    await session.refresh(share)
    return share


@router.delete("/folder-shares/{token}")
async def revoke_folder_share(
    token: str,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Revoke a share. Existing links stop resolving on the importer's next query.

    Permitted to the share's creator, or to anyone holding MEMBERS_INVITE in the
    source space — the existing "manages who may access this space" permission.
    A bare DOCUMENTS_READ holder must not be able to revoke someone else's share.
    """
    result = await session.execute(
        select(SharedFolder).filter(SharedFolder.token == token)
    )
    share = result.scalars().first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if share.created_by_id != auth.user.id:
        await check_permission(
            session,
            auth,
            share.source_search_space_id,
            Permission.MEMBERS_INVITE.value,
            "You don't have permission to revoke this share",
        )

    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
        await session.commit()

    return {"message": "Share revoked successfully"}


@router.post(
    "/search-spaces/{search_space_id}/folder-links", response_model=FolderLinkRead
)
async def create_folder_link(
    search_space_id: int,
    request: FolderLinkCreate,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Accept a share token, linking its folder subtree into this search space.

    Requires DOCUMENTS_CREATE on the *target* space: linking adds readable content
    to it, so the caller must be entitled to add content there. No permission is
    required in the source space — holding a live token is the grant.
    """
    await check_permission(
        session,
        auth,
        search_space_id,
        Permission.DOCUMENTS_CREATE.value,
        "You don't have permission to import folders into this search space",
    )

    result = await session.execute(
        select(SharedFolder).filter(
            SharedFolder.token == request.token,
            SharedFolder.revoked_at.is_(None),
            or_(
                SharedFolder.expires_at.is_(None),
                SharedFolder.expires_at > func.now(),
            ),
        )
    )
    share = result.scalars().first()
    if not share:
        raise HTTPException(status_code=404, detail="Share token is invalid or expired")

    if share.max_uses is not None and share.uses_count >= share.max_uses:
        raise HTTPException(
            status_code=409, detail="Share token has reached its use limit"
        )

    # A space linking its own folder is a no-op that would make the folder match
    # both halves of the read predicate's or_(), double-counting its documents.
    if share.source_search_space_id == search_space_id:
        raise HTTPException(
            status_code=409,
            detail="Cannot link a folder into the search space that owns it",
        )

    folder = await session.get(Folder, share.source_folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Shared folder no longer exists")

    link = FolderLink(
        share_id=share.id,
        source_folder_id=share.source_folder_id,
        target_search_space_id=search_space_id,
        created_by_id=auth.user.id,
    )
    session.add(link)
    share.uses_count += 1

    try:
        await session.commit()
    except IntegrityError:
        # uq_folder_link_target_source: already linked. Idempotent, and must not
        # burn a use.
        await session.rollback()
        existing = await session.execute(
            select(FolderLink).filter(
                FolderLink.target_search_space_id == search_space_id,
                FolderLink.source_folder_id == share.source_folder_id,
            )
        )
        link = existing.scalars().first()
        if not link:
            raise
        return _link_read(link, folder.name)

    await session.refresh(link)
    return _link_read(link, folder.name)
