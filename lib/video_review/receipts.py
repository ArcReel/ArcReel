"""Evidence that a particular immutable video was sampled, not a quality verdict."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lib.infra.content_digest import canonical_json_digest, sha256_file
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import safe_join


class FrameReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str
    version: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_times: tuple[float, ...] = Field(min_length=1, max_length=96)


def save_frame_receipt(
    project_path: Path, *, unit_id: str, version: int, video: Path, frame_times: tuple[float, ...]
) -> str:
    receipt = FrameReviewReceipt(
        unit_id=unit_id, version=version, content_hash=sha256_file(video), frame_times=frame_times
    )
    digest = canonical_json_digest(receipt.model_dump(mode="json"))
    path = safe_join(project_path, ".cache", "frame_reviews", f"{digest}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, receipt.model_dump(mode="json"))
    return digest


def verify_frame_receipt(
    project_path: Path, receipt_hash: str, *, unit_id: str, version: int, video: Path
) -> FrameReviewReceipt:
    if len(receipt_hash) != 64 or any(char not in "0123456789abcdef" for char in receipt_hash):
        raise ValueError("Invalid frame review receipt")
    path = safe_join(project_path, ".cache", "frame_reviews", f"{receipt_hash}.json", require_file=True)
    with path.open("rb") as handle:
        data = handle.read(16385)
    if len(data) > 16384:
        raise ValueError("Invalid frame review receipt")
    receipt = FrameReviewReceipt.model_validate_json(data)
    if (
        receipt.unit_id != unit_id
        or receipt.version != version
        or canonical_json_digest(receipt.model_dump(mode="json")) != receipt_hash
        or sha256_file(video) != receipt.content_hash
    ):
        raise ValueError("The inspected video changed; inspect this version again")
    return receipt
