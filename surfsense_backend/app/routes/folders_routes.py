"""API routes for folder CRUD, move, reorder, and document move operations."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.context import AuthContext
from app.db import Document, Folder, Permission, get_async_session
from app.schemas import (
    BulkDocumentMove,
    DocumentMove,
    FolderBreadcrumb,
    FolderCreate,
    FolderMove,
    FolderRead,
    FolderReorder,
    FolderScopeUpdate,
    FolderUpdate,
)
from app.services.folder_scope_service import promote_folder_to_space
from app.services.folder_sharing_service import (
    get_admin_visible_roots,
    get_linked_folder_ids,
    is_folder_linked,
    linked_folder_ids_subquery,
)
from app.services.folder_service import (
    check_no_circular_reference,
    dispatch_folder_deletion,
    generate_folder_position,
    get_folder_subtree_ids,
    get_subtree_max_depth,
    validate_folder_depth,
)
from app.users import get_auth_context
from app.utils.rbac import check_permission

router = APIRouter()


@router.post("/folders", response_model=FolderRead)
async def create_folder(
    request: FolderCreate,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    user = auth.user
    """Create a new folder. Requires DOCUMENTS_CREATE permission."""
    try:
        await check_permission(
            session,
            auth,
            request.search_space_id,
            Permission.DOCUMENTS_CREATE.value,
            "You don't have permission to create folders in this search space",
        )

        if request.parent_id is not None:
            parent = await session.get(Folder, request.parent_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent folder not found")
            if parent.search_space_id != request.search_space_id:
                raise HTTPException(
                    status_code=400,
                    detail="Parent folder belongs to a different search space",
                )

        await validate_folder_depth(session, request.parent_id)

        position = await generate_folder_position(
            session, request.search_space_id, request.parent_id
        )

        folder = Folder(
            name=request.name,
            position=position,
            parent_id=request.parent_id,
            search_space_id=request.search_space_id,
            created_by_id=user.id,
        )
        session.add(folder)
        await session.commit()
        await session.refresh(folder)
        return folder

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        if "uq_folder_space_parent_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail="A folder with this name already exists at this location",
            ) from e
        raise HTTPException(
            status_code=500, detail=f"Failed to create folder: {e!s}"
        ) from e


@router.get("/folders", response_model=list[FolderRead])
async def list_folders(
    search_space_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """List all folders in a search space (flat). Requires DOCUMENTS_READ permission."""
    try:
        await check_permission(
            session,
            auth,
            search_space_id,
            Permission.DOCUMENTS_READ.value,
            "You don't have permission to read folders in this search space",
        )

        result = await session.execute(
            select(Folder)
            .where(Folder.search_space_id == search_space_id)
            .order_by(Folder.position)
        )
        return result.scalars().all()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list folders: {e!s}"
        ) from e


class FolderTreeNode(BaseModel):
    id: int
    name: str
    # None at every mount root. For a foreign folder the real parent lives in a
    # space this one cannot read, so it is cleared rather than left dangling.
    parent_id: int | None
    owner_thread_id: int | None = None
    # own: this space's folder. linked: read through a share link. user: a
    # non-admin user's folder, read by a system admin.
    origin: Literal["own", "linked", "user"]
    owner_email: str | None = None  # set when origin == "user"
    document_count: int = 0  # documents directly inside, not the whole subtree


class FolderDocumentRead(BaseModel):
    id: int
    title: str
    document_type: str
    status: dict[str, Any] | None = None
    updated_at: datetime | None = None


@router.get(
    "/search-spaces/{search_space_id}/folder-tree",
    response_model=list[FolderTreeNode],
)
async def get_folder_tree(
    search_space_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Every folder this space can read, flat, for the client to nest by ``parent_id``.

    The space's own folders, the subtrees it holds a live share link to, and —
    for a system admin — every non-admin user's space-wide folders. The foreign
    part is exactly ``linked_folder_ids_subquery``, the grant retrieval uses, so
    the tree never shows a folder a question can't read or hides one it can.
    Requires DOCUMENTS_READ.
    """
    await check_permission(
        session,
        auth,
        search_space_id,
        Permission.DOCUMENTS_READ.value,
        "You don't have permission to read folders in this search space",
    )

    own = (
        (
            await session.execute(
                select(Folder).where(Folder.search_space_id == search_space_id)
            )
        )
        .scalars()
        .all()
    )
    foreign_ids = set(await get_linked_folder_ids(session, search_space_id))
    foreign = (
        (await session.execute(select(Folder).where(Folder.id.in_(foreign_ids))))
        .scalars()
        .all()
        if foreign_ids
        else []
    )
    user_roots = {
        folder.id: email
        for folder, email in await get_admin_visible_roots(session, search_space_id)
    }

    counts = dict(
        (
            await session.execute(
                select(Document.folder_id, func.count(Document.id))
                .where(
                    or_(
                        Document.search_space_id == search_space_id,
                        Document.folder_id.in_(
                            linked_folder_ids_subquery(search_space_id)
                        ),
                    ),
                    Document.folder_id.is_not(None),
                    func.coalesce(Document.status["state"].astext, "ready")
                    != "deleting",
                )
                .group_by(Document.folder_id)
            )
        ).all()
    )

    nodes = [
        FolderTreeNode(
            id=f.id,
            name=f.name,
            parent_id=f.parent_id,
            owner_thread_id=f.owner_thread_id,
            origin="own",
            document_count=counts.get(f.id, 0),
        )
        for f in own
    ]

    by_id = {f.id: f for f in foreign}

    def mount_root(folder: Folder) -> Folder:
        seen: set[int] = set()
        while folder.parent_id in by_id and folder.id not in seen:
            seen.add(folder.id)
            folder = by_id[folder.parent_id]
        return folder

    for f in foreign:
        owner_email = user_roots.get(mount_root(f).id)
        nodes.append(
            FolderTreeNode(
                id=f.id,
                name=f.name,
                parent_id=f.parent_id if f.parent_id in by_id else None,
                owner_thread_id=f.owner_thread_id,
                origin="user" if owner_email else "linked",
                owner_email=owner_email,
                document_count=counts.get(f.id, 0),
            )
        )
    return nodes


@router.get(
    "/search-spaces/{search_space_id}/folders/{folder_id}/documents",
    response_model=list[FolderDocumentRead],
)
async def list_folder_documents(
    search_space_id: int,
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Documents directly inside one folder of ``folder-tree``. Requires DOCUMENTS_READ.

    Answers for exactly the folders the tree lists — owned by this space, or read
    through a link or an admin's view. Anything else is a 404, so the route can't
    be used to probe other spaces' folder ids.
    """
    await check_permission(
        session,
        auth,
        search_space_id,
        Permission.DOCUMENTS_READ.value,
        "You don't have permission to read documents in this search space",
    )

    folder = await session.get(Folder, folder_id)
    readable = folder is not None and (
        folder.search_space_id == search_space_id
        or await is_folder_linked(session, search_space_id, folder_id)
    )
    if not readable:
        raise HTTPException(status_code=404, detail="Folder not found")

    rows = (
        await session.execute(
            select(
                Document.id,
                Document.title,
                Document.document_type,
                Document.status,
                Document.updated_at,
            )
            .where(Document.folder_id == folder_id)
            .order_by(Document.title)
        )
    ).all()
    return [
        FolderDocumentRead(
            id=row.id,
            title=row.title or "untitled",
            document_type=str(getattr(row.document_type, "value", row.document_type)),
            status=row.status,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/folders/{folder_id}", response_model=FolderRead)
async def get_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Get a single folder. Requires DOCUMENTS_READ permission."""
    try:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await check_permission(
            session,
            auth,
            folder.search_space_id,
            Permission.DOCUMENTS_READ.value,
            "You don't have permission to read folders in this search space",
        )

        return folder

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get folder: {e!s}"
        ) from e


@router.get("/folders/{folder_id}/breadcrumb", response_model=list[FolderBreadcrumb])
async def get_folder_breadcrumb(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Get ancestor chain for breadcrumb display. Requires DOCUMENTS_READ permission."""
    try:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await check_permission(
            session,
            auth,
            folder.search_space_id,
            Permission.DOCUMENTS_READ.value,
            "You don't have permission to read folders in this search space",
        )

        result = await session.execute(
            text("""
                WITH RECURSIVE ancestors AS (
                    SELECT id, name, parent_id, 0 AS depth
                    FROM folders WHERE id = :folder_id
                    UNION ALL
                    SELECT f.id, f.name, f.parent_id, a.depth + 1
                    FROM folders f JOIN ancestors a ON f.id = a.parent_id
                )
                SELECT id, name FROM ancestors ORDER BY depth DESC;
            """),
            {"folder_id": folder_id},
        )
        rows = result.fetchall()
        return [FolderBreadcrumb(id=row.id, name=row.name) for row in rows]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get breadcrumb: {e!s}"
        ) from e


@router.patch("/folders/{folder_id}/watched")
async def stop_watching_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Clear the watched flag from a folder's metadata."""
    folder = await session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    await check_permission(
        session,
        auth,
        folder.search_space_id,
        Permission.DOCUMENTS_UPDATE.value,
        "You don't have permission to update folders in this search space",
    )

    if folder.folder_metadata and isinstance(folder.folder_metadata, dict):
        updated = {**folder.folder_metadata, "watched": False}
        folder.folder_metadata = updated
    await session.commit()

    return {"message": "Folder watch status updated"}


@router.put("/folders/{folder_id}", response_model=FolderRead)
async def update_folder(
    folder_id: int,
    request: FolderUpdate,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Rename a folder. Requires DOCUMENTS_UPDATE permission."""
    try:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await check_permission(
            session,
            auth,
            folder.search_space_id,
            Permission.DOCUMENTS_UPDATE.value,
            "You don't have permission to update folders in this search space",
        )

        folder.name = request.name
        await session.commit()
        await session.refresh(folder)
        return folder

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        if "uq_folder_space_parent_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail="A folder with this name already exists at this location",
            ) from e
        raise HTTPException(
            status_code=500, detail=f"Failed to update folder: {e!s}"
        ) from e


@router.put("/folders/{folder_id}/move", response_model=FolderRead)
async def move_folder(
    folder_id: int,
    request: FolderMove,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Move a folder to a new parent. Requires DOCUMENTS_UPDATE permission."""
    try:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await check_permission(
            session,
            auth,
            folder.search_space_id,
            Permission.DOCUMENTS_UPDATE.value,
            "You don't have permission to move folders in this search space",
        )

        if request.new_parent_id is not None:
            new_parent = await session.get(Folder, request.new_parent_id)
            if not new_parent:
                raise HTTPException(
                    status_code=404, detail="Target parent folder not found"
                )
            if new_parent.search_space_id != folder.search_space_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot move folder to a different search space",
                )

        await check_no_circular_reference(session, folder_id, request.new_parent_id)
        subtree_depth = await get_subtree_max_depth(session, folder_id)
        await validate_folder_depth(session, request.new_parent_id, subtree_depth)

        position = await generate_folder_position(
            session, folder.search_space_id, request.new_parent_id
        )
        folder.parent_id = request.new_parent_id
        folder.position = position

        # Session scope follows the destination: a subtree must stay uniform
        # (every visibility check assumes children carry their root's stamp), so
        # moving under a parent restamps the whole subtree to the parent's
        # scope. Moving to root keeps the current scope.
        if request.new_parent_id is not None:
            new_parent = await session.get(Folder, request.new_parent_id)
            target_owner = new_parent.owner_thread_id if new_parent else None
            if target_owner != folder.owner_thread_id:
                subtree_ids = await get_folder_subtree_ids(session, folder_id)
                await session.execute(
                    Folder.__table__.update()
                    .where(Folder.id.in_(subtree_ids))
                    .values(owner_thread_id=target_owner)
                )

        await session.commit()
        await session.refresh(folder)
        return folder

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        if "uq_folder_space_parent_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail="A folder with this name already exists at the target location",
            ) from e
        raise HTTPException(
            status_code=500, detail=f"Failed to move folder: {e!s}"
        ) from e


@router.put("/folders/{folder_id}/reorder", response_model=FolderRead)
async def reorder_folder(
    folder_id: int,
    request: FolderReorder,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Reorder a folder among its siblings via fractional indexing. Requires DOCUMENTS_UPDATE."""
    try:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await check_permission(
            session,
            auth,
            folder.search_space_id,
            Permission.DOCUMENTS_UPDATE.value,
            "You don't have permission to reorder folders in this search space",
        )

        position = await generate_folder_position(
            session,
            folder.search_space_id,
            folder.parent_id,
            before_position=request.before_position,
            after_position=request.after_position,
        )
        folder.position = position
        await session.commit()
        await session.refresh(folder)
        return folder

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to reorder folder: {e!s}"
        ) from e


@router.patch("/folders/{folder_id}/scope")
async def update_folder_scope(
    folder_id: int,
    request: FolderScopeUpdate,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Promote a session-scoped folder subtree to space-wide ("general knowledge").

    Nothing is copied or re-embedded — the session stamp is cleared and the
    documents' identity hashes are recomputed to their space-wide form. After
    this, every chat session in the space sees the folder. Requires
    DOCUMENTS_UPDATE. There is no demotion; re-upload into a session instead.
    """
    del request  # scope can only be "space"; validated by the schema
    folder = await session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    await check_permission(
        session,
        auth,
        folder.search_space_id,
        Permission.DOCUMENTS_UPDATE.value,
        "You don't have permission to update folders in this search space",
    )

    result = await promote_folder_to_space(session, folder)
    return {"message": f"Folder '{folder.name}' is now space-wide", **result}


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Mark documents for deletion and dispatch Celery to delete docs first, then folders."""
    try:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await check_permission(
            session,
            auth,
            folder.search_space_id,
            Permission.DOCUMENTS_DELETE.value,
            "You don't have permission to delete folders in this search space",
        )

        queued = await dispatch_folder_deletion(session, folder_id)

        return {
            "message": "Folder deletion started",
            "documents_queued_for_deletion": queued,
        }

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to delete folder: {e!s}"
        ) from e


@router.put("/documents/{document_id}/move")
async def move_document(
    document_id: int,
    request: DocumentMove,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Move a document to a folder (or root). Requires DOCUMENTS_UPDATE permission."""
    try:
        result = await session.execute(
            select(Document).filter(Document.id == document_id)
        )
        document = result.scalars().first()
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        await check_permission(
            session,
            auth,
            document.search_space_id,
            Permission.DOCUMENTS_UPDATE.value,
            "You don't have permission to move documents in this search space",
        )

        if request.folder_id is not None:
            target = await session.get(Folder, request.folder_id)
            if not target:
                raise HTTPException(status_code=404, detail="Target folder not found")
            if target.search_space_id != document.search_space_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot move document to a folder in a different search space",
                )

        document.folder_id = request.folder_id
        await session.commit()
        return {"message": "Document moved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to move document: {e!s}"
        ) from e


@router.put("/documents/bulk-move")
async def bulk_move_documents(
    request: BulkDocumentMove,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """Move multiple documents to a folder (or root). Requires DOCUMENTS_UPDATE permission."""
    try:
        if not request.document_ids:
            raise HTTPException(status_code=400, detail="No document IDs provided")

        result = await session.execute(
            select(Document).filter(Document.id.in_(request.document_ids))
        )
        documents = result.scalars().all()

        if not documents:
            raise HTTPException(status_code=404, detail="No documents found")

        search_space_ids = {doc.search_space_id for doc in documents}
        for ss_id in search_space_ids:
            await check_permission(
                session,
                auth,
                ss_id,
                Permission.DOCUMENTS_UPDATE.value,
                "You don't have permission to move documents in this search space",
            )

        if request.folder_id is not None:
            target = await session.get(Folder, request.folder_id)
            if not target:
                raise HTTPException(status_code=404, detail="Target folder not found")
            mismatched = [
                doc.id
                for doc in documents
                if doc.search_space_id != target.search_space_id
            ]
            if mismatched:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot move documents to a folder in a different search space",
                )

        for doc in documents:
            doc.folder_id = request.folder_id
        await session.commit()
        return {"message": f"{len(request.document_ids)} documents moved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to move documents: {e!s}"
        ) from e
