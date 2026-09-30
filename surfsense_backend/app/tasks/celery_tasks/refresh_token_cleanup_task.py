"""Celery task for pruning expired refresh-token rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_

from app.celery_app import celery_app
from app.config import config
from app.db import RefreshToken, async_session_maker
from app.tasks.celery_tasks import run_async_celery_task


@celery_app.task(name="purge_refresh_tokens")
def purge_refresh_tokens() -> int:
    return run_async_celery_task(_purge_refresh_tokens)


async def _purge_refresh_tokens() -> int:
    now = datetime.now(UTC)
    revoked_cutoff = now - timedelta(seconds=config.REFRESH_ROTATION_GRACE_SECONDS)

    async with async_session_maker() as session:
        result = await session.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < now,
                    RefreshToken.revoked_at < revoked_cutoff,
                )
            )
        )
        await session.commit()
        return result.rowcount or 0
