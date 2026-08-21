"""Repository for durable application-level background jobs."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from lib.db.base import dt_to_iso, utc_now
from lib.db.models.background_job import BackgroundJob
from lib.db.repositories.base import BaseRepository, rowcount

ACTIVE_BACKGROUND_JOB_STATUSES = ("queued", "running")


def background_job_to_dict(job: BackgroundJob) -> dict[str, Any]:
    try:
        result = json.loads(job.result_json) if job.result_json else None
    except (TypeError, ValueError):
        result = None
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "phase": job.phase,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "result": result,
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "queued_at": dt_to_iso(job.queued_at),
        "started_at": dt_to_iso(job.started_at),
        "finished_at": dt_to_iso(job.finished_at),
        "updated_at": dt_to_iso(job.updated_at),
    }


class BackgroundJobRepository(BaseRepository):
    async def enqueue(self, job_type: str) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        job = BackgroundJob(
            job_id=uuid.uuid4().hex,
            job_type=job_type,
            status="queued",
            phase="queued",
            progress_current=0,
            progress_total=0,
            queued_at=now,
            updated_at=now,
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_active(job_type)
            if existing is not None:
                return existing, True
            raise
        return background_job_to_dict(job), False

    async def get_active(self, job_type: str) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == job_type,
                BackgroundJob.status.in_(ACTIVE_BACKGROUND_JOB_STATUSES),
            )
            .order_by(BackgroundJob.queued_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return background_job_to_dict(row) if row else None

    async def get_latest(self, job_type: str) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(BackgroundJob)
            .where(BackgroundJob.job_type == job_type)
            .order_by(BackgroundJob.updated_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return background_job_to_dict(row) if row else None

    async def claim_next(self) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(BackgroundJob.job_id)
            .where(BackgroundJob.status == "queued")
            .order_by(BackgroundJob.queued_at.asc())
            .limit(1)
        )
        job_id = result.scalar_one_or_none()
        if job_id is None:
            return None
        now = utc_now()
        claimed = await self.session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.job_id == job_id, BackgroundJob.status == "queued")
            .values(status="running", phase="fetching_catalog", started_at=now, updated_at=now)
        )
        if rowcount(claimed) == 0:
            await self.session.rollback()
            return None
        await self.session.commit()
        row = await self.session.get(BackgroundJob, job_id)
        return background_job_to_dict(row) if row else None

    async def update_progress(self, job_id: str, *, phase: str, current: int, total: int) -> bool:
        now = utc_now()
        result = await self.session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.job_id == job_id, BackgroundJob.status == "running")
            .values(
                phase=phase,
                progress_current=max(0, current),
                progress_total=max(0, total),
                updated_at=now,
            )
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def mark_succeeded(self, job_id: str, result_payload: dict[str, Any]) -> bool:
        now = utc_now()
        result = await self.session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.job_id == job_id, BackgroundJob.status == "running")
            .values(
                status="succeeded",
                phase="completed",
                result_json=json.dumps(result_payload, ensure_ascii=False),
                progress_current=BackgroundJob.progress_total,
                finished_at=now,
                updated_at=now,
            )
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def mark_failed(self, job_id: str, *, error_code: str, error_detail: str | None = None) -> bool:
        now = utc_now()
        result = await self.session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.job_id == job_id, BackgroundJob.status == "running")
            .values(
                status="failed",
                phase="failed",
                error_code=error_code,
                error_detail=error_detail,
                finished_at=now,
                updated_at=now,
            )
        )
        await self.session.commit()
        return rowcount(result) > 0

    async def recover_interrupted(self) -> int:
        now = utc_now()
        result = await self.session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.status == "running")
            .values(status="queued", phase="queued", started_at=None, updated_at=now)
        )
        await self.session.commit()
        return rowcount(result)
