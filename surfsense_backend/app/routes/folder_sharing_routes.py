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


def _link_read(link: FolderLink, folder_name: str, live: bool = True) -> FolderLinkRead:
    return FolderLinkRead(
        id=link.id,
        share_id=link.share_id,
        source_folder_id=link.source_folder_id,
        target_search_space_id=link.target_search_space_id,
        created_by_id=link.created_by_id,
        created_at=link.created_at,
        folder_name=folder_name,
        live=live,
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

    # A session-scoped folder is invisible even to the owner's other chats;
    # letting it cross a user boundary before being deliberately promoted
    # would be a surprising widening. Promote first, then share.
    if folder.owner_thread_id is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This folder is scoped to a single chat session. Promote it to "
                "space-wide (PATCH /folders/{id}/scope) before sharing."
            ),
        )

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


@router.get(
    "/search-spaces/{search_space_id}/folder-links",
    response_model=list[FolderLinkRead],
)
async def list_folder_links(
    search_space_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Folders imported into this search space. Requires DOCUMENTS_READ.

    Imported folders live in the *sharer's* space, so they appear in neither
    ``/folders`` nor ``/documents/watched-folders`` for the importer — without this
    a client has no way to know what it has imported, or to undo it.

    ``live`` reports whether the underlying share still resolves: a revoked or
    expired share leaves its link in place but stops returning documents, and a
    client that showed it as healthy would be lying about what is searchable.
    """
    await check_permission(
        session,
        auth,
        search_space_id,
        Permission.DOCUMENTS_READ.value,
        "You don't have permission to read this search space",
    )

    rows = (
        await session.execute(
            select(FolderLink, Folder.name, SharedFolder)
            .join(Folder, Folder.id == FolderLink.source_folder_id)
            .join(SharedFolder, SharedFolder.id == FolderLink.share_id)
            .filter(FolderLink.target_search_space_id == search_space_id)
            .order_by(FolderLink.id)
        )
    ).all()

    now = datetime.now(UTC)
    return [
        _link_read(
            link,
            folder_name,
            live=share.revoked_at is None
            and (share.expires_at is None or share.expires_at > now),
        )
        for link, folder_name, share in rows
    ]


@router.delete("/folder-links/{link_id}")
async def delete_folder_link(
    link_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Drop an imported folder from the search space that imported it.

    Removes only the link. The sharer's folder and documents are untouched — this
    is the importer withdrawing their own copy of the grant, not a deletion — and
    it can be undone by redeeming the token again.

    Requires DOCUMENTS_DELETE on the *target* space: the link makes content
    readable there, so removing it is a change to that space's content.
    """
    link = await session.get(FolderLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Imported folder not found")

    await check_permission(
        session,
        auth,
        link.target_search_space_id,
        Permission.DOCUMENTS_DELETE.value,
        "You don't have permission to remove imported folders from this search space",
    )

    await session.delete(link)
    await session.commit()
    return {"message": "Imported folder removed"}


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

    # Read every attribute we still need *before* committing. commit() expires
    # these instances, and a later attribute access would then issue a lazy
    # refresh — sync IO on an async session, which raises MissingGreenlet.
    folder_name = folder.name
    source_folder_id = share.source_folder_id

    link = FolderLink(
        share_id=share.id,
        source_folder_id=source_folder_id,
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
                FolderLink.source_folder_id == source_folder_id,
            )
        )
        link = existing.scalars().first()
        if not link:
            raise
        return _link_read(link, folder_name)

    await session.refresh(link)
    return _link_read(link, folder_name)
