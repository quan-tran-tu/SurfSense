"""System-admin API: manage users, their folders, and cross-user folder shares.

Every route here is gated by :func:`app.users.require_admin` (``is_superuser``),
which is seeded from ``config.ADMIN_EMAILS`` on login. Unlike the rest of the API,
these routes deliberately reach *across* the per-user search-space isolation
boundary — that is the whole point of an admin — so they skip the usual
``check_permission`` / membership gates and rely solely on the superuser flag.

The one capability normal sharing lacks is a *hard* revoke: a user's
``DELETE /folder-shares/{token}`` only sets ``revoked_at`` (links remain but stop
resolving). The admin's ``DELETE /admin/folder-shares/{id}`` also deletes every
``FolderLink``, so the shared folder is genuinely removed from every importer's
knowledge base, not merely silenced.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.context import AuthContext
from app.db import (
    Document,
    Folder,
    FolderLink,
    SearchSpace,
    SharedFolder,
    User,
    get_async_session,
)
from app.schemas.users import UserCreate
from app.services.folder_service import dispatch_folder_deletion
from app.users import UserManager, get_user_manager, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class AdminUserRead(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    is_active: bool
    is_superuser: bool
    is_verified: bool
    last_login: datetime | None = None
    search_space_count: int = 0
    folder_count: int = 0
    document_count: int = 0


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = Field(default=None, max_length=255)
    is_superuser: bool = False


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    is_superuser: bool | None = None
    # An empty string clears the name.
    display_name: str | None = Field(default=None, max_length=255)


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=8)


class AdminFolderRead(BaseModel):
    id: int
    name: str
    parent_id: int | None = None
    search_space_id: int
    search_space_name: str | None = None
    created_by_id: str | None = None
    owner_thread_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminShareRead(BaseModel):
    id: int
    token: str
    name: str | None = None
    source_folder_id: int
    source_search_space_id: int
    created_by_id: str | None = None
    created_by_email: str | None = None
    expires_at: datetime | None = None
    max_uses: int | None = None
    uses_count: int
    revoked_at: datetime | None = None
    link_count: int = 0
    created_at: datetime


class AdminLinkRead(BaseModel):
    id: int
    share_id: int
    source_folder_id: int
    source_folder_name: str | None = None
    target_search_space_id: int
    target_owner_email: str | None = None
    created_by_id: str | None = None
    created_at: datetime


# ── Helpers ─────────────────────────────────────────────────────────────────


def _count_columns(owner_id):
    """Spaces, folders and documents owned by ``owner_id``, as scalar subqueries.

    Scalar subqueries rather than join+group_by: under GOOGLE auth ``User`` eager-
    loads ``oauth_accounts`` (lazy="joined"), and a grouped join would multiply
    every count by the number of oauth rows. ``owner_id`` is either ``User.id``
    (correlated, for the list) or a literal id (for one user).
    """
    spaces = select(func.count(SearchSpace.id)).where(SearchSpace.user_id == owner_id)
    folders = (
        select(func.count(Folder.id))
        .select_from(Folder)
        .join(SearchSpace, SearchSpace.id == Folder.search_space_id)
        .where(SearchSpace.user_id == owner_id)
    )
    documents = (
        select(func.count(Document.id))
        .select_from(Document)
        .join(SearchSpace, SearchSpace.id == Document.search_space_id)
        .where(SearchSpace.user_id == owner_id)
    )
    return [
        q.correlate_except(SearchSpace, Folder, Document).scalar_subquery()
        for q in (spaces, folders, documents)
    ]


def _user_read(
    user: User, spaces: int, folders: int = 0, documents: int = 0
) -> AdminUserRead:
    return AdminUserRead(
        id=str(user.id),
        email=user.email,
        display_name=getattr(user, "display_name", None),
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
        is_verified=bool(user.is_verified),
        last_login=getattr(user, "last_login", None),
        search_space_count=spaces,
        folder_count=folders,
        document_count=documents,
    )


async def _read_user(session: AsyncSession, user: User) -> AdminUserRead:
    spaces, folders, documents = (
        await session.execute(select(*_count_columns(user.id)))
    ).one()
    return _user_read(user, spaces, folders, documents)


async def _count_admins(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count(User.id)).filter(
                User.is_superuser.is_(True), User.is_active.is_(True)
            )
        )
    ).scalar_one()


# ── Users ───────────────────────────────────────────────────────────────────


@router.get("/users", response_model=list[AdminUserRead])
async def list_users(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Every user, with counts of the spaces, folders and documents they own."""
    rows = (
        await session.execute(select(User, *_count_columns(User.id)).order_by(User.email))
    ).all()
    return [
        _user_read(user, spaces, folders, documents)
        for user, spaces, folders, documents in rows
    ]


@router.post("/users", response_model=AdminUserRead, status_code=201)
async def create_user(
    body: AdminUserCreate,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
    auth: AuthContext = Depends(require_admin),
):
    """Create an account on someone's behalf — the way in when registration is off.

    Goes through the user manager, so the password is hashed exactly as on
    self-registration and ``on_after_register`` seeds the default search space.
    The account is created verified; ``safe=False`` is what lets an admin set
    ``is_superuser`` at creation.
    """
    try:
        user = await user_manager.create(
            UserCreate(
                email=body.email,
                password=body.password,
                is_superuser=body.is_superuser,
                is_verified=True,
            ),
            safe=False,
        )
    except UserAlreadyExists as err:
        raise HTTPException(
            status_code=409, detail="A user with this email already exists"
        ) from err
    except InvalidPasswordException as err:
        raise HTTPException(
            status_code=400, detail=f"Invalid password: {err.reason}"
        ) from err

    if body.display_name and body.display_name.strip():
        user = await user_manager.user_db.update(
            user, {"display_name": body.display_name.strip()}
        )

    logger.info(
        f"Admin {auth.user.email} created user {user.email}"
        + (" (admin)" if body.is_superuser else "")
    )
    return await _read_user(session, user)


@router.patch("/users/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Activate/deactivate a user, grant/revoke system-admin, or rename them.

    Guards against self-lockout: an admin cannot strip their own admin or
    deactivate themselves, and the last active admin cannot be demoted at all.
    """
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_self = str(user.id) == str(auth.user.id)
    removing_admin = body.is_superuser is False and user.is_superuser
    deactivating = body.is_active is False and user.is_active

    if is_self and removing_admin:
        raise HTTPException(
            status_code=400, detail="You cannot remove your own admin access"
        )
    if is_self and deactivating:
        raise HTTPException(
            status_code=400, detail="You cannot deactivate your own account"
        )
    # Don't let the last remaining admin be demoted/deactivated by anyone.
    if user.is_superuser and (removing_admin or deactivating):
        if await _count_admins(session) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the last active system admin",
            )

    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superuser is not None:
        user.is_superuser = body.is_superuser
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    await session.commit()
    await session.refresh(user)
    return await _read_user(session, user)


@router.post("/users/{user_id}/password")
async def reset_user_password(
    user_id: uuid.UUID,
    body: AdminPasswordReset,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
    auth: AuthContext = Depends(require_admin),
):
    """Set a new password for a user; they sign in with it from now on.

    Sessions already issued are not ended: access tokens are stateless JWTs that
    run to expiry. To lock someone out *now*, deactivate the account too —
    ``get_auth_context`` rejects inactive users on every request.
    """
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        await user_manager.validate_password(body.password, user)
    except InvalidPasswordException as err:
        raise HTTPException(
            status_code=400, detail=f"Invalid password: {err.reason}"
        ) from err

    user.hashed_password = user_manager.password_helper.hash(body.password)
    email = user.email
    await session.commit()
    logger.info(f"Admin {auth.user.email} reset the password of {email}")
    return {"message": f"Password updated for {email}"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Delete a user and everything they own (search spaces, folders, documents,
    chats — via FK cascades). Irreversible.

    Refuses to delete the calling admin, and refuses to delete the last active
    admin. Documents are removed by the ``searchspaces`` CASCADE, so no Celery
    cleanup is dispatched here — the rows simply go.
    """
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if str(user.id) == str(auth.user.id):
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if user.is_superuser and await _count_admins(session) <= 1:
        raise HTTPException(
            status_code=400, detail="Cannot delete the last active system admin"
        )

    email = user.email
    try:
        await session.delete(user)
        await session.commit()
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Could not delete user: a referencing record blocks it.",
        ) from err
    logger.info(f"Admin {auth.user.email} deleted user {email} ({user_id})")
    return {"message": f"Deleted user {email}"}


@router.get("/users/{user_id}/folders", response_model=list[AdminFolderRead])
async def list_user_folders(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Folders in every search space this user owns."""
    if not await session.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")

    rows = (
        await session.execute(
            select(Folder, SearchSpace.name)
            .join(SearchSpace, SearchSpace.id == Folder.search_space_id)
            .filter(SearchSpace.user_id == user_id)
            .order_by(Folder.search_space_id, Folder.name)
        )
    ).all()
    return [
        AdminFolderRead(
            id=f.id,
            name=f.name,
            parent_id=f.parent_id,
            search_space_id=f.search_space_id,
            search_space_name=space_name,
            created_by_id=str(f.created_by_id) if f.created_by_id else None,
            owner_thread_id=f.owner_thread_id,
            created_at=f.created_at,
        )
        for f, space_name in rows
    ]


@router.patch("/folders/{folder_id}/scope")
async def promote_folder_scope(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Promote any user's session-scoped folder to space-wide, bypassing membership.

    Same operation as the owner's ``PATCH /folders/{id}/scope`` — clears the
    session stamp on the subtree and rehashes the documents' identities.
    """
    from app.services.folder_scope_service import promote_folder_to_space

    folder = await session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    result = await promote_folder_to_space(session, folder)
    logger.info(
        f"Admin {auth.user.email} promoted folder #{folder_id} to space-wide"
    )
    return {"message": f"Folder '{folder.name}' is now space-wide", **result}


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Delete any folder (and its documents), bypassing space membership.

    Reuses the same Celery-dispatched deletion the owner's route uses, so
    documents are actually removed rather than orphaned.
    """
    folder = await session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    queued = await dispatch_folder_deletion(session, folder_id)
    logger.info(f"Admin {auth.user.email} deleted folder #{folder_id}")
    return {
        "message": "Folder deletion started",
        "documents_queued_for_deletion": queued,
    }


# ── Shares ──────────────────────────────────────────────────────────────────


@router.get("/folder-shares", response_model=list[AdminShareRead])
async def list_folder_shares(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Every folder share ever minted, newest first, with its importer count."""
    rows = (
        await session.execute(
            select(SharedFolder, User.email, func.count(FolderLink.id))
            .outerjoin(User, User.id == SharedFolder.created_by_id)
            .outerjoin(FolderLink, FolderLink.share_id == SharedFolder.id)
            .group_by(SharedFolder.id, User.email)
            .order_by(SharedFolder.id.desc())
        )
    ).all()
    return [
        AdminShareRead(
            id=s.id,
            token=s.token,
            name=s.name,
            source_folder_id=s.source_folder_id,
            source_search_space_id=s.source_search_space_id,
            created_by_id=str(s.created_by_id) if s.created_by_id else None,
            created_by_email=email,
            expires_at=s.expires_at,
            max_uses=s.max_uses,
            uses_count=s.uses_count,
            revoked_at=s.revoked_at,
            link_count=link_count,
            created_at=s.created_at,
        )
        for s, email, link_count in rows
    ]


@router.delete("/folder-shares/{share_id}")
async def hard_revoke_share(
    share_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Hard-revoke a share: mark it revoked AND delete every link to it.

    This is the "truly revoke" the normal user route can't do — a plain revoke
    only sets ``revoked_at`` and leaves the ``FolderLink`` rows in place (they
    stop resolving but linger). Here the links are deleted, so the shared folder
    is removed from every importer's knowledge base outright.
    """
    share = await session.get(SharedFolder, share_id)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    removed = (
        await session.execute(
            select(func.count(FolderLink.id)).filter(FolderLink.share_id == share_id)
        )
    ).scalar_one()

    await session.execute(delete(FolderLink).where(FolderLink.share_id == share_id))
    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
    await session.commit()

    logger.info(
        f"Admin {auth.user.email} hard-revoked share #{share_id} "
        f"({removed} link(s) removed)"
    )
    return {
        "message": "Share revoked and removed from all importers",
        "links_removed": removed,
    }


@router.get("/folder-links", response_model=list[AdminLinkRead])
async def list_folder_links(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Every accepted import: which folder is linked into whose search space."""
    owner = aliased(User)
    rows = (
        await session.execute(
            select(FolderLink, Folder.name, owner.email)
            .outerjoin(Folder, Folder.id == FolderLink.source_folder_id)
            .outerjoin(
                SearchSpace, SearchSpace.id == FolderLink.target_search_space_id
            )
            .outerjoin(owner, owner.id == SearchSpace.user_id)
            .order_by(FolderLink.id.desc())
        )
    ).all()
    return [
        AdminLinkRead(
            id=link.id,
            share_id=link.share_id,
            source_folder_id=link.source_folder_id,
            source_folder_name=folder_name,
            target_search_space_id=link.target_search_space_id,
            target_owner_email=owner_email,
            created_by_id=str(link.created_by_id) if link.created_by_id else None,
            created_at=link.created_at,
        )
        for link, folder_name, owner_email in rows
    ]


@router.delete("/folder-links/{link_id}")
async def delete_folder_link(
    link_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Remove one imported folder from the search space that imported it.

    The sharer's folder and documents are untouched — this drops a single
    importer's access, not the share itself.
    """
    link = await session.get(FolderLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Imported folder not found")

    await session.delete(link)
    await session.commit()
    logger.info(f"Admin {auth.user.email} removed folder link #{link_id}")
    return {"message": "Imported folder removed"}
