"""System-admin API: manage users, their folders, and cross-user folder shares.

Every route here is gated by :func:`app.users.require_admin` (``is_superuser``),
whose first holder is seeded by ``app.bootstrap_admin`` at deploy time. Unlike the rest of the API,
these routes deliberately reach *across* the per-user search-space isolation
boundary — that is the whole point of an admin — so they skip the usual
``check_permission`` / membership gates and rely solely on the superuser flag.

A share's expiry is set once, by whoever mints it, and never edited; the admin's
one lever on a share is revoke. A user's ``DELETE /folder-shares/{token}`` only
sets ``revoked_at`` (links remain but stop resolving). The admin's
``DELETE /admin/folder-shares/{id}`` also deletes every ``FolderLink``, so the
shared folder is removed from every importer's knowledge base, not merely
silenced.

The other admin-only capability is *user groups*: a named set of users, plus
folder grants against it. Granting a folder to a group hands every member
read-only access with no token and no acceptance step — the way an admin
publishes "general" documents to a team. Membership itself grants nothing:
two members of a group still cannot see each other's personal folders unless a
share token or a grant says so. See ``app.services.folder_sharing_service``.

Admins are never group members: they already read every non-admin user's
folders, and they are the ones who hand out grants. Adding one is refused, and
promoting a member to admin drops their memberships.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.context import AuthContext
from app.db import (
    Document,
    Folder,
    FolderGroupGrant,
    FolderLink,
    SearchSpace,
    SharedFolder,
    SharedFolderImport,
    User,
    UserGroup,
    UserGroupMembership,
    get_async_session,
)
from app.schemas.users import UserCreate
from app.services.app_settings_service import (
    RETENTION_KINDS,
    clear_retention_override,
    env_retention_days,
    get_retention_override,
    set_retention_override,
)
from app.services.folder_service import dispatch_folder_deletion
from app.services.folder_sharing_service import (
    record_imports_stopped,
    share_state,
)
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
    promoted_from_thread_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminRootFolderRead(BaseModel):
    id: int
    name: str
    owner_email: str | None = None
    document_count: int = 0
    group_ids: list[int] = []


class AdminShareRead(BaseModel):
    id: int
    token: str
    name: str | None = None
    source_folder_id: int
    source_folder_name: str | None = None
    source_search_space_id: int
    created_by_id: str | None = None
    created_by_email: str | None = None
    expires_at: datetime | None = None
    max_uses: int | None = None
    uses_count: int
    revoked_at: datetime | None = None
    link_count: int = 0
    created_at: datetime
    state: str = "live"  # live | revoked | expired — the last two are final


class RetentionDays(BaseModel):
    # Days per folder kind; None or 0 keeps that kind forever.
    session: int | None = Field(default=None, ge=0)
    group: int | None = Field(default=None, ge=0)
    admin: int | None = Field(default=None, ge=0)
    space: int | None = Field(default=None, ge=0)


class RetentionSettings(BaseModel):
    effective: RetentionDays
    deployment: RetentionDays  # the env defaults
    overridden: bool  # whether an admin's settings replace the env defaults


class AdminGroupRead(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_by_id: str | None = None
    member_count: int = 0
    folder_count: int = 0
    created_at: datetime


class AdminGroupWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class AdminGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    # An empty string clears the description.
    description: str | None = Field(default=None, max_length=500)


class AdminGroupMemberRead(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    is_superuser: bool = False
    added_at: datetime


class AdminGroupMemberAdd(BaseModel):
    user_id: uuid.UUID


class AdminGroupFolderRead(BaseModel):
    folder_id: int
    name: str
    search_space_id: int
    owner_email: str | None = None
    document_count: int = 0
    granted_at: datetime


class AdminGroupFolderAdd(BaseModel):
    folder_id: int


class AdminLinkRead(BaseModel):
    id: int
    share_id: int
    source_folder_id: int
    source_folder_name: str | None = None
    target_search_space_id: int
    target_owner_email: str | None = None
    created_by_id: str | None = None
    created_at: datetime


class AdminShareImportRead(BaseModel):
    """One importer of a share token, kept after their link is gone."""

    id: int
    share_id: int
    user_email: str | None = None
    first_imported_at: datetime
    last_imported_at: datetime
    import_count: int
    # using | removed | removed_by_admin | revoked (by an admin) — or, while the
    # link is still there, the ended share's state: revoked | expired.
    status: str
    stopped_at: datetime | None = None
    # The current link, while there is one, so the admin can remove it.
    link_id: int | None = None


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
        if body.is_superuser and not user.is_superuser:
            # Admins are never group members (see the module docstring).
            await session.execute(
                delete(UserGroupMembership).where(
                    UserGroupMembership.user_id == user.id
                )
            )
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
            promoted_from_thread_id=f.promoted_from_thread_id,
            created_at=f.created_at,
        )
        for f, space_name in rows
    ]


@router.get("/folders", response_model=list[AdminRootFolderRead])
async def list_root_folders(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Every user's space-wide root folders, with the groups each is granted to.

    These are the folders an admin can grant or share; session-scoped ones are
    left out because the server refuses both until they are promoted. The
    document count is shallow, as in ``list_group_folders``.
    """
    documents = (
        select(func.count(Document.id))
        .where(Document.folder_id == Folder.id)
        .correlate_except(Document)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Folder.id, Folder.name, User.email, documents)
            .join(SearchSpace, SearchSpace.id == Folder.search_space_id)
            .outerjoin(User, User.id == SearchSpace.user_id)
            .where(Folder.parent_id.is_(None), Folder.owner_thread_id.is_(None))
            .order_by(Folder.name)
        )
    ).all()
    groups: dict[int, list[int]] = {}
    for folder_id, group_id in (
        await session.execute(select(FolderGroupGrant.folder_id, FolderGroupGrant.group_id))
    ).all():
        groups.setdefault(folder_id, []).append(group_id)
    return [
        AdminRootFolderRead(
            id=folder_id,
            name=name,
            owner_email=email,
            document_count=count,
            group_ids=groups.get(folder_id, []),
        )
        for folder_id, name, email, count in rows
    ]


@router.patch("/folders/{folder_id}/scope")
async def promote_folder_scope(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Promote any user's session-scoped folder to space-wide.

    Same operation as the owner's ``PATCH /folders/{id}/scope``, bypassing
    membership. Promote only: undoing a promotion is the owner's call, made from
    their own sidebar.
    """
    from app.services.folder_scope_service import promote_folder_to_space

    folder = await session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    name = folder.name

    result = await promote_folder_to_space(session, folder)
    done = f"Folder '{name}' is now space-wide"
    await session.commit()
    logger.info(f"Admin {auth.user.email}: {done} (#{folder_id})")
    return {"message": done, **result}


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
    await session.commit()
    logger.info(f"Admin {auth.user.email} deleted folder #{folder_id}")
    return {
        "message": "Folder deletion started",
        "documents_queued_for_deletion": queued,
    }


# ── Groups ──────────────────────────────────────────────────────────


async def _get_group(session: AsyncSession, group_id: int) -> UserGroup:
    group = await session.get(UserGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def _group_counts():
    """Member and folder-grant counts for a group, as correlated scalar subqueries."""
    members = select(func.count(UserGroupMembership.id)).where(
        UserGroupMembership.group_id == UserGroup.id
    )
    folders = select(func.count(FolderGroupGrant.id)).where(
        FolderGroupGrant.group_id == UserGroup.id
    )
    return [
        q.correlate_except(UserGroupMembership, FolderGroupGrant).scalar_subquery()
        for q in (members, folders)
    ]


def _group_read(group: UserGroup, members: int = 0, folders: int = 0) -> AdminGroupRead:
    return AdminGroupRead(
        id=group.id,
        name=group.name,
        description=group.description,
        created_by_id=str(group.created_by_id) if group.created_by_id else None,
        member_count=members,
        folder_count=folders,
        created_at=group.created_at,
    )


@router.get("/groups", response_model=list[AdminGroupRead])
async def list_groups(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Every user group, with how many members it has and how many folders it holds."""
    rows = (
        await session.execute(
            select(UserGroup, *_group_counts()).order_by(UserGroup.name)
        )
    ).all()
    return [_group_read(g, members, folders) for g, members, folders in rows]


@router.post("/groups", response_model=AdminGroupRead, status_code=201)
async def create_group(
    body: AdminGroupWrite,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Create an empty user group. Names are unique across the deployment."""
    group = UserGroup(
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        created_by_id=auth.user.id,
    )
    session.add(group)
    try:
        await session.commit()
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A group with this name already exists"
        ) from err
    await session.refresh(group)
    logger.info(f"Admin {auth.user.email} created group '{group.name}'")
    return _group_read(group)


@router.patch("/groups/{group_id}", response_model=AdminGroupRead)
async def update_group(
    group_id: int,
    body: AdminGroupUpdate,
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Rename a group or change its description. Members and grants are untouched."""
    group = await _get_group(session, group_id)
    if body.name is not None:
        group.name = body.name.strip()
    if body.description is not None:
        group.description = body.description.strip() or None
    try:
        await session.commit()
    except IntegrityError as err:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A group with this name already exists"
        ) from err
    await session.refresh(group)
    members, folders = (
        await session.execute(select(*_group_counts()).where(UserGroup.id == group_id))
    ).one()
    return _group_read(group, members, folders)


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Delete a group; its memberships and folder grants go with it (CASCADE).

    No documents are touched — a grant was only ever a read-through, so deleting
    it removes the members' access and nothing else. Access ends on their next
    query, since membership and grants are read per query.
    """
    group = await _get_group(session, group_id)
    name = group.name
    await session.delete(group)
    await session.commit()
    logger.info(f"Admin {auth.user.email} deleted group '{name}'")
    return {"message": f"Deleted group {name}"}


@router.get("/groups/{group_id}/members", response_model=list[AdminGroupMemberRead])
async def list_group_members(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Who is in this group."""
    await _get_group(session, group_id)
    rows = (
        await session.execute(
            select(UserGroupMembership, User)
            .join(User, User.id == UserGroupMembership.user_id)
            .where(UserGroupMembership.group_id == group_id)
            .order_by(User.email)
        )
    ).all()
    return [
        AdminGroupMemberRead(
            user_id=str(user.id),
            email=user.email,
            display_name=getattr(user, "display_name", None),
            is_superuser=bool(user.is_superuser),
            added_at=membership.created_at,
        )
        for membership, user in rows
    ]


@router.post("/groups/{group_id}/members", status_code=201)
async def add_group_member(
    group_id: int,
    body: AdminGroupMemberAdd,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Add a user to a group. They see the group's folders on their next question.

    Adding someone twice is a no-op rather than an error, so a double-click in
    the admin panel does not surface a failure.
    """
    group = await _get_group(session, group_id)
    user = await session.get(User, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_superuser:
        raise HTTPException(
            status_code=400,
            detail=f"{user.email} is an admin — admins are not added to groups",
        )

    session.add(
        UserGroupMembership(
            group_id=group_id, user_id=user.id, added_by_id=auth.user.id
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"message": f"{user.email} is already in {group.name}"}
    logger.info(f"Admin {auth.user.email} added {user.email} to group '{group.name}'")
    return {"message": f"Added {user.email} to {group.name}"}


@router.delete("/groups/{group_id}/members/{user_id}")
async def remove_group_member(
    group_id: int,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Remove a user from a group; the group's folders leave their view at once."""
    await _get_group(session, group_id)
    removed = (
        await session.execute(
            delete(UserGroupMembership).where(
                UserGroupMembership.group_id == group_id,
                UserGroupMembership.user_id == user_id,
            )
        )
    ).rowcount
    if not removed:
        raise HTTPException(status_code=404, detail="User is not in this group")
    await session.commit()
    logger.info(f"Admin {auth.user.email} removed {user_id} from group #{group_id}")
    return {"message": "Removed from group"}


@router.get("/groups/{group_id}/folders", response_model=list[AdminGroupFolderRead])
async def list_group_folders(
    group_id: int,
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Folders granted to this group, with their owner and document count.

    The count is of documents directly inside the granted folder. Subfolders are
    granted too — the grant covers the whole subtree — but the shallow count is
    what the folder tree shows per node, and it keeps this route cheap.
    """
    await _get_group(session, group_id)
    documents = (
        select(func.count(Document.id))
        .where(Document.folder_id == Folder.id)
        .correlate_except(Document)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(FolderGroupGrant, Folder, User.email, documents)
            .join(Folder, Folder.id == FolderGroupGrant.folder_id)
            .outerjoin(SearchSpace, SearchSpace.id == Folder.search_space_id)
            .outerjoin(User, User.id == SearchSpace.user_id)
            .where(FolderGroupGrant.group_id == group_id)
            .order_by(Folder.name)
        )
    ).all()
    return [
        AdminGroupFolderRead(
            folder_id=folder.id,
            name=folder.name,
            search_space_id=folder.search_space_id,
            owner_email=owner_email,
            document_count=document_count,
            granted_at=grant.created_at,
        )
        for grant, folder, owner_email, document_count in rows
    ]


@router.post("/groups/{group_id}/folders", status_code=201)
async def grant_folder_to_group(
    group_id: int,
    body: AdminGroupFolderAdd,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Grant a folder subtree to a group: every member reads it, read-only.

    This is how "general" documents reach a team — the admin uploads them into
    one of their own folders and grants that folder here. Any folder an admin can
    see may be granted, a user's included; granting is always a deliberate admin
    act, which is why group membership alone never exposes anyone's uploads.

    Session-scoped folders are refused for the same reason sharing refuses them:
    they are invisible even to their owner's other chats, so handing one to a
    group would widen it past what its owner sees. Promote it first.
    """
    group = await _get_group(session, group_id)
    folder = await session.get(Folder, body.folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if folder.owner_thread_id is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "This folder is scoped to a single chat session. Promote it to "
                "space-wide before granting it to a group."
            ),
        )

    session.add(
        FolderGroupGrant(
            group_id=group_id, folder_id=folder.id, granted_by_id=auth.user.id
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"message": f"'{folder.name}' is already granted to {group.name}"}
    logger.info(
        f"Admin {auth.user.email} granted folder #{folder.id} to group '{group.name}'"
    )
    return {"message": f"Granted '{folder.name}' to {group.name}"}


@router.delete("/groups/{group_id}/folders/{folder_id}")
async def revoke_folder_from_group(
    group_id: int,
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Revoke a folder grant. Members lose the folder on their next query.

    Nothing is deleted but the grant row: the folder and its documents stay where
    they are, owned by whoever owned them.
    """
    await _get_group(session, group_id)
    removed = (
        await session.execute(
            delete(FolderGroupGrant).where(
                FolderGroupGrant.group_id == group_id,
                FolderGroupGrant.folder_id == folder_id,
            )
        )
    ).rowcount
    if not removed:
        raise HTTPException(status_code=404, detail="Grant not found")
    await session.commit()
    logger.info(
        f"Admin {auth.user.email} revoked folder #{folder_id} from group #{group_id}"
    )
    return {"message": "Folder revoked from group"}


# ── Shares ──────────────────────────────────────────────────────────────────


@router.get("/folder-shares", response_model=list[AdminShareRead])
async def list_folder_shares(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Every folder share ever minted, newest first, with its importer count."""
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(SharedFolder, User.email, Folder.name, func.count(FolderLink.id))
            .outerjoin(User, User.id == SharedFolder.created_by_id)
            .outerjoin(Folder, Folder.id == SharedFolder.source_folder_id)
            .outerjoin(FolderLink, FolderLink.share_id == SharedFolder.id)
            .group_by(SharedFolder.id, User.email, Folder.name)
            .order_by(SharedFolder.id.desc())
        )
    ).all()
    return [
        AdminShareRead(
            id=s.id,
            token=s.token,
            name=s.name,
            source_folder_id=s.source_folder_id,
            source_folder_name=folder_name,
            source_search_space_id=s.source_search_space_id,
            created_by_id=str(s.created_by_id) if s.created_by_id else None,
            created_by_email=email,
            expires_at=s.expires_at,
            max_uses=s.max_uses,
            uses_count=s.uses_count,
            revoked_at=s.revoked_at,
            link_count=link_count,
            created_at=s.created_at,
            state=share_state(s, now),
        )
        for s, email, folder_name, link_count in rows
    ]


@router.delete("/folder-shares/{share_id}")
async def revoke_share(
    share_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Revoke a share: mark it revoked AND delete every link to it.

    More than the owner's own revoke, which only sets ``revoked_at`` and leaves
    the ``FolderLink`` rows in place (they stop resolving but linger). Here the
    links are deleted, so the folder leaves every importer's Imported list.
    """
    share = await session.get(SharedFolder, share_id)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    removed = (
        await session.execute(
            select(func.count(FolderLink.id)).filter(FolderLink.share_id == share_id)
        )
    ).scalar_one()

    await record_imports_stopped(session, share_id, "revoked")
    await session.execute(delete(FolderLink).where(FolderLink.share_id == share_id))
    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
    await session.commit()

    logger.info(
        f"Admin {auth.user.email} revoked share #{share_id} ({removed} link(s) removed)"
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


@router.get("/folder-share-imports", response_model=list[AdminShareImportRead])
async def list_folder_share_imports(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """Everyone who ever imported a share token, and whether they still read it.

    ``status`` is ``using`` while the link stands and the share is live; the
    ended share's state while the link stands on a revoked or expired share;
    otherwise how the link was dropped.
    """
    importer = aliased(User)
    space_owner = aliased(User)
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(
                SharedFolderImport,
                SharedFolder,
                FolderLink.id,
                func.coalesce(importer.email, space_owner.email),
            )
            .join(SharedFolder, SharedFolder.id == SharedFolderImport.share_id)
            .outerjoin(
                FolderLink,
                and_(
                    FolderLink.share_id == SharedFolderImport.share_id,
                    FolderLink.target_search_space_id
                    == SharedFolderImport.target_search_space_id,
                ),
            )
            .outerjoin(importer, importer.id == SharedFolderImport.user_id)
            .outerjoin(
                SearchSpace, SearchSpace.id == SharedFolderImport.target_search_space_id
            )
            .outerjoin(space_owner, space_owner.id == SearchSpace.user_id)
            .order_by(SharedFolderImport.created_at)
        )
    ).all()
    out = []
    for imp, share, link_id, email in rows:
        state = share_state(share, now)
        if link_id is None:
            status = imp.stop_reason or "removed"
        else:
            status = "using" if state == "live" else state
        out.append(
            AdminShareImportRead(
                id=imp.id,
                share_id=imp.share_id,
                user_email=email,
                first_imported_at=imp.created_at,
                last_imported_at=imp.last_imported_at,
                import_count=imp.import_count,
                status=status,
                stopped_at=imp.stopped_at,
                link_id=link_id,
            )
        )
    return out


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

    await record_imports_stopped(
        session, link.share_id, "removed_by_admin", link.target_search_space_id
    )
    await session.delete(link)
    await session.commit()
    logger.info(f"Admin {auth.user.email} removed folder link #{link_id}")
    return {"message": "Imported folder removed"}


# ── Settings ────────────────────────────────────────────────────────────────


async def _retention_settings(session: AsyncSession) -> RetentionSettings:
    override = await get_retention_override(session)
    deployment = env_retention_days()
    return RetentionSettings(
        effective=RetentionDays(**(override or deployment)),
        deployment=RetentionDays(**deployment),
        overridden=override is not None,
    )


@router.get("/settings/retention", response_model=RetentionSettings)
async def get_retention_settings(
    session: AsyncSession = Depends(get_async_session),
    _: AuthContext = Depends(require_admin),
):
    """How long each kind of folder is kept, and whether that is the env default."""
    return await _retention_settings(session)


@router.put("/settings/retention", response_model=RetentionSettings)
async def put_retention_settings(
    body: RetentionDays,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Replace the env defaults with these periods (None or 0 = keep forever).

    The daily retention run (03:53) applies them; nothing is deleted on save.
    """
    await set_retention_override(
        session, {k: getattr(body, k) for k in RETENTION_KINDS}, auth.user.id
    )
    logger.info(f"Admin {auth.user.email} set folder retention to {body.model_dump()}")
    return await _retention_settings(session)


@router.delete("/settings/retention", response_model=RetentionSettings)
async def reset_retention_settings(
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(require_admin),
):
    """Drop the admin's periods; the deployment's env defaults apply again."""
    await clear_retention_override(session)
    logger.info(f"Admin {auth.user.email} reset folder retention to the deployment defaults")
    return await _retention_settings(session)
