"""Persistent episode and item identity across edits and trash restoration."""

from tempfile import TemporaryDirectory

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from lib.episode.episode_deletion import delete_episode, episode_deletion_impact, episode_trash_record
from lib.episode.episode_management import create_episode, move_episode
from lib.project.project_manager import ProjectManager
from lib.project.project_trash import restore
from lib.script.script_editor import move_segment, remove_segment
from tests.invariant_profiles import is_nightly_profile, state_machine_settings

pytestmark = pytest.mark.invariant


class EpisodeHistory(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.root = TemporaryDirectory()
        self.pm = ProjectManager(self.root.name)
        self.pm.create_project("demo", content_mode="narration")
        self.pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        self.project_dir = self.pm.get_project_path("demo")
        self.order: list[int] = []
        self.deleted: list[int] = []
        self.items: dict[int, list[str]] = {}
        self.last_item: dict[int, int] = {}
        self.last_episode = 0
        self.add_episode()
        self.add_item()

    def teardown(self):
        self.root.cleanup()

    @rule()
    def add_episode(self):
        self.last_episode += 1
        episode = create_episode(self.pm, "demo", title=f"Title {self.last_episode}")
        assert episode == self.last_episode
        self.order.append(episode)
        self.items[episode] = []
        self.last_item[episode] = 0
        self.pm.save_script(
            "demo",
            {"episode": episode, "title": f"Title {episode}", "content_mode": "narration", "segments": []},
            f"episode_{episode}.json",
        )

    @precondition(lambda self: bool(self.order))
    @rule(index=st.integers(0, 15))
    def delete(self, index):
        episode = self.order.pop(index % len(self.order))
        project = self.pm.load_project("demo")
        impact = episode_deletion_impact(self.project_dir, project, episode)
        delete_episode(self.pm, "demo", episode, revision=impact.revision)
        self.deleted.append(episode)

    @precondition(lambda self: bool(self.deleted))
    @rule(index=st.integers(0, 15))
    def restore(self, index):
        episode = self.deleted.pop(index % len(self.deleted))
        record = episode_trash_record(self.project_dir, episode)
        assert record is not None
        restore(self.project_dir, record.id)
        self.order.append(episode)

    @precondition(lambda self: len(self.order) >= 2)
    @rule(index=st.integers(1, 15))
    def move_to_front(self, index):
        episode = self.order.pop(1 + (index - 1) % (len(self.order) - 1))
        move_episode(self.pm, "demo", episode, after=None)
        self.order.insert(0, episode)

    @precondition(lambda self: bool(self.order))
    @rule()
    def add_item(self):
        episode = self.order[-1]
        self.last_item[episode] += 1
        expected = f"E{episode}S{self.last_item[episode]:02d}"
        assert self.pm.reserve_item_ids("demo", episode, "segments", 1) == [expected]
        with self.pm.locked_script("demo", f"episode_{episode}.json") as script:
            script["segments"].append(
                {
                    "segment_id": expected,
                    "novel_text": f"Text {expected}",
                    "duration_seconds": 4,
                    "characters_in_segment": [],
                }
            )
        self.items[episode].append(expected)

    @precondition(lambda self: bool(self.order and self.items[self.order[-1]]))
    @rule()
    def remove_last_item(self):
        episode = self.order[-1]
        with self.pm.locked_script("demo", f"episode_{episode}.json") as script:
            remove_segment(script, self.items[episode][-1])
        self.items[episode].pop()

    @precondition(lambda self: bool(self.order) and len(self.items[self.order[-1]]) >= 2)
    @rule()
    def move_last_item_to_front(self):
        episode = self.order[-1]
        items = self.items[episode]
        with self.pm.locked_script("demo", f"episode_{episode}.json") as script:
            move_segment(script, items[-1], after_id=None)
        items.insert(0, items.pop())

    @invariant()
    def ledger_matches_history(self):
        project = self.pm.load_project("demo")
        assert project["episode_id_high_water"] == self.last_episode
        assert [(entry["episode"], entry["title"]) for entry in project["episodes"]] == [
            (ep, f"Title {ep}") for ep in self.order
        ]
        for entry in project["episodes"]:
            episode = entry["episode"]
            assert entry.get("item_id_high_water", 0) == self.last_item[episode]
            script = self.pm.load_script("demo", f"episode_{episode}.json")
            assert [(row["segment_id"], row["novel_text"]) for row in script["segments"]] == [
                (identity, f"Text {identity}") for identity in self.items[episode]
            ]


TestEpisodeHistory = EpisodeHistory.TestCase
TestEpisodeHistory.settings = state_machine_settings()
if is_nightly_profile():
    TestEpisodeHistory = pytest.mark.timeout(600)(TestEpisodeHistory)


def test_replay_delete_last_item_then_allocate():
    history = EpisodeHistory()
    try:
        history.remove_last_item()
        history.add_item()
        script = history.pm.load_script("demo", "episode_1.json")
        assert [row["segment_id"] for row in script["segments"]] == ["E1S02"]
    finally:
        history.teardown()
