"""SessionRekey moves local SDK sessions from any source-key directory to target, preserving existing target sessions."""

from __future__ import annotations

from pathlib import Path

from lib.agent.agent_session_store.rekey import SessionRekey


def _touch_session(key_dir: Path, session_id: str) -> None:
    (key_dir / session_id / "subagents").mkdir(parents=True)
    (key_dir / f"{session_id}.jsonl").write_text("{}\n", encoding="utf-8")


def test_local_transcripts_move_from_any_key_dir_and_skip_conflicts(tmp_path):
    projects = tmp_path / "projects"
    _touch_session(projects / "old", "s1")
    _touch_session(projects / "old", "other")
    _touch_session(projects / "old", "at-target")
    _touch_session(projects / "new", "at-target")

    moved = SessionRekey.of(["s1", "at-target"], "new").move_local_transcripts(tmp_path)

    assert moved == {"s1"}
    assert (projects / "new" / "s1.jsonl").is_file()
    assert (projects / "new" / "s1" / "subagents").is_dir()
    assert sorted(path.name for path in (projects / "old").iterdir()) == [
        "at-target",
        "at-target.jsonl",
        "other",
        "other.jsonl",
    ]
    assert SessionRekey.of(["s1"], "new").move_local_transcripts(tmp_path) == set()
