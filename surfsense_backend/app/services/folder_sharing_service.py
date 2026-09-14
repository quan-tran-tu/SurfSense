"""Cross-user folder sharing: token minting, liveness, and the linked-subtree CTE.

A ``SharedFolder`` is a grant; a ``FolderLink`` is its acceptance. Linked folders
are read *through* — no documents are copied — so every read surface must widen
its space filter from ``search_space_id == X`` to "owned by X, or inside a folder
X has a live link to". :func:`linked_folder_ids_subquery` is the single definition
of that second clause; use it rather than rebuilding the predicate.

System admins hold one more grant of the same shape, with no link row behind it:
a space owned by a superuser reads every non-admin user's space-wide folders (see
:func:`_admin_visible_roots`). It is folded into the same subquery, so every
surface that honours links — retrieval, the agent's read-only ``_shared`` mount,
citations, mention and scope pins, the write guard — honours it too, and none of
them can drift from the others.
"""

import secrets

from sqlalchemy import Select, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import (
    Document,
    Folder,
    FolderLink,
    SearchSpace,
    SearchSpaceMembership,
    SharedFolder,
    User,
)

# Linked folders are surfaced to the agent under this reserved prefix so they are
# visibly distinct from owned content, and so the write guard has a cheap
# path-prefix test to fall back on in addition to the authoritative id check.
SHARED_PREFIX = "_shared"


def generate_share_token() -> str:
    """Mint an opaque share token. 43 chars, fits ``shared_folders.token`` (64)."""
    return secrets.token_urlsafe(32)


def _in_spaces(column, search_space_id: int | Select | list[int]):
    """``column`` names the given space, one of several, or one a subquery selects."""
    if isinstance(search_space_id, int):
        return column == search_space_id
    return column.in_(search_space_id)


def _admin_visible_roots(search_space_id: int | Select | list[int]) -> Select:
    """Root folders a system admin's space reads without holding a link.

    Selects ``id`` and ``owner_email``. Empty unless the viewing space is owned by
    a superuser; then it is every root folder in a space owned by a user who is
    *not* one. Admins do not read each other's folders — the grant runs from a
    higher role to a lower one — and an admin's own folders are theirs already.

    Session-scoped roots are left out: they are invisible even to the owner's
    other chats until promoted, and an admin's view should not be wider than the
    owner's own. Children carry their root's stamp, so filtering roots suffices.

    Like link liveness, the admin flag is read per query: demoting an admin
    removes the grant on their very next question, not after some sweep.
    """
    viewer_space = aliased(SearchSpace)
    viewer = aliased(User)
    owner_space = aliased(SearchSpace)
    owner = aliased(User)

    viewer_is_admin = (
        select(viewer_space.id)
        .join(viewer, viewer.id == viewer_space.user_id)
        .where(
            _in_spaces(viewer_space.id, search_space_id),
            viewer.is_superuser.is_(True),
        )
        .exists()
    )
    return (
        select(Folder.id.label("id"), owner.email.label("owner_email"))
        .select_from(Folder)
        .join(owner_space, owner_space.id == Folder.search_space_id)
        .join(owner, owner.id == owner_space.user_id)
        .where(
            viewer_is_admin,
            owner.is_superuser.is_(False),
            Folder.parent_id.is_(None),
            Folder.owner_thread_id.is_(None),
        )
    )


def linked_folder_ids_subquery(search_space_id: int | Select | list[int]):
    """Selectable yielding every folder id readable via a link from the given space(s).

    Accepts one space id, several, or a subquery selecting them — the citation
    resolver has to ask "any space this user belongs to", because
    ``GET /documents/by-chunk/{id}`` carries no space context.

    Recursive: a link on ``Research`` grants ``Research/AI`` too. Liveness is
    checked here rather than swept in the background, so a revoked share stops
    resolving on the very next query — a stale grant is a security bug, not a
    performance one.

    ``max_uses`` is deliberately *not* checked. It caps how many spaces may accept
    a share, not whether an accepted link keeps working; exhausting it must not
    retroactively sever links that were already granted. This matches the
    ``SearchSpaceInvite`` precedent, where a used-up invite does not evict members.

    The roots also include :func:`_admin_visible_roots`, so for a system admin
    "readable via a link" means "readable without owning it" — linked, or a
    non-admin user's folder.

    Returns a selectable, not a coroutine, so it can be embedded directly as a
    subquery in an ``IN (...)`` predicate without a second round trip.
    """
    link_roots = (
        select(FolderLink.source_folder_id.label("id"))
        .join(SharedFolder, SharedFolder.id == FolderLink.share_id)
        .where(
            _in_spaces(FolderLink.target_search_space_id, search_space_id),
            SharedFolder.revoked_at.is_(None),
            or_(
                SharedFolder.expires_at.is_(None),
                SharedFolder.expires_at > func.now(),
            ),
        )
    )
    admin_roots = _admin_visible_roots(search_space_id).subquery("admin_roots")
    anchor = union_all(link_roots, select(admin_roots.c.id)).subquery("linked_roots")

    roots = select(anchor.c.id).cte("linked_folders", recursive=True)
    descendants = select(Folder.id).join(roots, Folder.parent_id == roots.c.id)
    linked = roots.union_all(descendants)
    return select(linked.c.id)


async def user_can_read_via_link(
    session: AsyncSession, user_id, document: Document
) -> bool:
    """True if ``user_id`` reaches ``document`` through a live link in any of their spaces.

    Backstops the citation resolver. A linked document keeps the *sharer's*
    ``search_space_id``, so the ordinary membership check on that space fails for
    the importer and the citation 403s on click — it fails closed, but it fails.
    Access here is real: the sharer granted it, and the user holds a live link in
    a space they belong to. The same holds for an admin reading a user's folder.
    """
    if document.folder_id is None:
        return False

    user_spaces = select(SearchSpaceMembership.search_space_id).where(
        SearchSpaceMembership.user_id == user_id
    )
    linked = linked_folder_ids_subquery(user_spaces).subquery()
    result = await session.execute(
        select(linked.c.id).where(linked.c.id == document.folder_id)
    )
    return result.first() is not None


async def live_link_fingerprint(
    session: AsyncSession, search_space_id: int
) -> tuple[int, int, int, int]:
    """Cheap value that changes whenever this space's live link set changes.

    Cache keys over the workspace tree must include this. Links are created and
    revoked through REST (``/import``, ``/unshare``), which never bumps
    ``tree_version`` — that only advances when the *agent* mutates documents. A
    cache keyed on ``tree_version`` alone would keep serving a tree rendered
    before the import, and because the tree cache lives on a long-lived
    middleware instance, it would do so in later conversations too.

    ``(count, max_id)`` moves on import (both) and on revoke or expiry (count
    drops, since liveness is part of the query). The second pair does the same
    for an admin's view of user folders: a new upload, a deleted root, or a user
    promoted to admin moves it.
    """
    result = await session.execute(
        select(
            func.count(FolderLink.id),
            func.coalesce(func.max(FolderLink.id), 0),
        )
        .select_from(FolderLink)
        .join(SharedFolder, SharedFolder.id == FolderLink.share_id)
        .where(
            FolderLink.target_search_space_id == search_space_id,
            SharedFolder.revoked_at.is_(None),
            or_(
                SharedFolder.expires_at.is_(None),
                SharedFolder.expires_at > func.now(),
            ),
        )
    )
    row = result.one()

    admin_roots = _admin_visible_roots(search_space_id).subquery()
    admin_row = (
        await session.execute(
            select(
                func.count(admin_roots.c.id),
                func.coalesce(func.max(admin_roots.c.id), 0),
            )
        )
    ).one()
    return (int(row[0]), int(row[1]), int(admin_row[0]), int(admin_row[1]))


async def get_linked_folder_roots(
    session: AsyncSession, search_space_id: int
) -> list[Folder]:
    """The folders directly named by a live link — the subtree roots.

    Distinct from :func:`get_linked_folder_ids`, which also returns descendants.
    Callers building virtual paths need the roots specifically: a root is what
    gets mounted at ``/documents/_shared/<name>``, and descendants hang off it.
    Admin-visible user folders are not links; see :func:`get_admin_visible_roots`.
    """
    result = await session.execute(
        select(Folder)
        .join(FolderLink, FolderLink.source_folder_id == Folder.id)
        .join(SharedFolder, SharedFolder.id == FolderLink.share_id)
        .where(
            FolderLink.target_search_space_id == search_space_id,
            SharedFolder.revoked_at.is_(None),
            or_(
                SharedFolder.expires_at.is_(None),
                SharedFolder.expires_at > func.now(),
            ),
        )
    )
    return list(result.scalars().all())


async def get_admin_visible_roots(
    session: AsyncSession, search_space_id: int
) -> list[tuple[Folder, str]]:
    """The roots :func:`_admin_visible_roots` grants, each with its owner's email.

    Empty unless ``search_space_id`` is owned by a system admin. The email is what
    callers group by: the agent mounts these at
    ``/documents/_shared/<email>/<name>``, and the web client shows the email as a
    folder holding them.
    """
    roots = _admin_visible_roots(search_space_id).subquery()
    result = await session.execute(
        select(Folder, roots.c.owner_email)
        .join(roots, roots.c.id == Folder.id)
        .order_by(roots.c.owner_email, Folder.name)
    )
    return [(folder, owner_email) for folder, owner_email in result.all()]


async def get_linked_folder_ids(
    session: AsyncSession, search_space_id: int
) -> list[int]:
    """Materialize :func:`linked_folder_ids_subquery` for callers needing a list.

    Prefer the subquery form where the predicate is going into SQL anyway; this
    exists for Python-side work (path trees, write guards) that must hold the ids.
    """
    result = await session.execute(linked_folder_ids_subquery(search_space_id))
    return list(result.scalars().all())


async def is_folder_linked(
    session: AsyncSession, search_space_id: int, folder_id: int
) -> bool:
    """True if ``folder_id`` is readable by ``search_space_id`` only via a link.

    The write guard's authoritative question: linked folders are read-only, so any
    mutation resolving to one must be refused.
    """
    subq = linked_folder_ids_subquery(search_space_id).subquery()
    result = await session.execute(select(subq.c.id).where(subq.c.id == folder_id))
    return result.first() is not None
