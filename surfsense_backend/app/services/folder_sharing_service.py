"""Cross-user folder sharing: token minting, liveness, and the linked-subtree CTE.

A ``SharedFolder`` is a grant; a ``FolderLink`` is its acceptance. Linked folders
are read *through* — no documents are copied — so every read surface must widen
its space filter from ``search_space_id == X`` to "owned by X, or inside a folder
X has a live link to". :func:`linked_folder_ids_subquery` is the single definition
of that second clause; use it rather than rebuilding the predicate.
"""

import secrets

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    Document,
    Folder,
    FolderLink,
    SearchSpaceMembership,
    SharedFolder,
)

# Linked folders are surfaced to the agent under this reserved prefix so they are
# visibly distinct from owned content, and so the write guard has a cheap
# path-prefix test to fall back on in addition to the authoritative id check.
SHARED_PREFIX = "_shared"


def generate_share_token() -> str:
    """Mint an opaque share token. 43 chars, fits ``shared_folders.token`` (64)."""
    return secrets.token_urlsafe(32)


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

    Returns a selectable, not a coroutine, so it can be embedded directly as a
    subquery in an ``IN (...)`` predicate without a second round trip.
    """
    if isinstance(search_space_id, int):
        target = FolderLink.target_search_space_id == search_space_id
    else:
        target = FolderLink.target_search_space_id.in_(search_space_id)

    roots = (
        select(FolderLink.source_folder_id.label("id"))
        .join(SharedFolder, SharedFolder.id == FolderLink.share_id)
        .where(
            target,
            SharedFolder.revoked_at.is_(None),
            or_(
                SharedFolder.expires_at.is_(None),
                SharedFolder.expires_at > func.now(),
            ),
        )
        .cte("linked_folders", recursive=True)
    )
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
    a space they belong to.
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
) -> tuple[int, int]:
    """Cheap value that changes whenever this space's live link set changes.

    Cache keys over the workspace tree must include this. Links are created and
    revoked through REST (``/import``, ``/unshare``), which never bumps
    ``tree_version`` — that only advances when the *agent* mutates documents. A
    cache keyed on ``tree_version`` alone would keep serving a tree rendered
    before the import, and because the tree cache lives on a long-lived
    middleware instance, it would do so in later conversations too.

    ``(count, max_id)`` moves on import (both) and on revoke or expiry (count
    drops, since liveness is part of the query).
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
    return (int(row[0]), int(row[1]))


async def get_linked_folder_roots(
    session: AsyncSession, search_space_id: int
) -> list[Folder]:
    """The folders directly named by a live link — the subtree roots.

    Distinct from :func:`get_linked_folder_ids`, which also returns descendants.
    Callers building virtual paths need the roots specifically: a root is what
    gets mounted at ``/documents/_shared/<name>``, and descendants hang off it.
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
