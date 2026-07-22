import hashlib

from app.indexing_pipeline.connector_document import ConnectorDocument


def compute_identifier_hash(
    document_type_value: str, unique_id: str, search_space_id: int
) -> str:
    """Return a stable SHA-256 hash from raw identity components."""
    combined = f"{document_type_value}:{unique_id}:{search_space_id}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def local_file_unique_id(
    folder_name: str, relative_path: str, owner_thread_id: int | None = None
) -> str:
    """Identity string for a LOCAL_FOLDER_FILE document.

    ``unique_identifier_hash`` is globally unique, and session-scoped folders
    let two chat sessions each upload a root named ``folder_name`` with the
    same files inside — so the owning thread must be part of the identity or
    the second session's upload silently updates the first session's rows.
    A space-wide folder (``owner_thread_id is None``) keeps the historical
    unprefixed form, so existing documents keep resolving.
    """
    base = f"{folder_name}:{relative_path}"
    if owner_thread_id is None:
        return base
    return f"thread:{owner_thread_id}:{base}"


def compute_unique_identifier_hash(doc: ConnectorDocument) -> str:
    """Return a stable SHA-256 hash identifying a document by its source identity."""
    return compute_identifier_hash(
        doc.document_type.value, doc.unique_id, doc.search_space_id
    )


def compute_content_hash(doc: ConnectorDocument) -> str:
    """Return a SHA-256 hash of the document's content scoped to its search space."""
    combined = f"{doc.search_space_id}:{doc.source_markdown}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
