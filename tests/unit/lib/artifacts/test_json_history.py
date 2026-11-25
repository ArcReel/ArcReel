import json
from pathlib import Path

from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter
from lib.artifacts.legacy_history_index import LegacyHistoryIndex
from lib.material.storage import ObjectLocator
from tests.version_history_contract_cases import VersionHistoryContractCases


class TestJsonHistory(VersionHistoryContractCases):
    sqlite = False


def test_legacy_record_offsets_across_utf8_chunk_boundaries(tmp_path):
    path = tmp_path / "versions.json"
    records = [{"version": n, "prompt": "星" * 32000 + str(n)} for n in (1, 2, 3)]
    path.write_text(json.dumps({"videos": {"星": {"versions": records, "current_version": 3}}}, ensure_ascii=False))
    result = LegacyHistoryIndex().resources(path, [("videos", "星")], equivalent=False, proof_version=1)
    assert result["videos"]["星"]["versions"] == [records[2], records[0]]


def test_evicted_remote_file_is_admitted_without_reading_whole_history(tmp_path, monkeypatch):
    path = tmp_path / "versions" / "versions.json"
    path.parent.mkdir()
    snapshot = "versions/characters/hero_v1.png"
    record = {
        "version": 1,
        "storage": ObjectLocator(1, "bucket", "object").to_record(),
        "content_version_id": "hero-v1",
        "content_digest": "a" * 64,
        "file": snapshot,
    }
    path.write_text(json.dumps({"characters": {"hero": {"versions": [record], "current_version": 1}}}))
    read_bytes = Path.read_bytes

    def read_individual_file(file):
        if file == path:
            raise AssertionError("remote proof read the entire JSON history")
        return read_bytes(file)

    monkeypatch.setattr(Path, "read_bytes", read_individual_file)
    observed = ProjectArtifactManifestAdapter(tmp_path).inspect_artifact_content(snapshot)
    assert observed.present
    assert observed.content_digest == record["content_digest"]
