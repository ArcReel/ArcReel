from unittest.mock import AsyncMock, call

from server.services.tasks import narration_delivery_tasks


async def test_active_tts_observation_spans_script_locator_spellings() -> None:
    queue = AsyncMock()

    async def _query(**kwargs):
        if kwargs["script_file"] == "scripts/episode_1.json":
            return [{"resource_id": "E1U1", "script_file": kwargs["script_file"]}]
        return []

    queue.get_active_tasks_for_resources.side_effect = _query
    active = await narration_delivery_tasks.active_tts_resource_ids(
        project_name="demo",
        resource_ids=("E1U1", "E1U1", ""),
        script_file="episode_1.json",
        queue=queue,
    )

    assert active == frozenset({"E1U1"})
    assert queue.get_active_tasks_for_resources.await_args_list == [
        call(
            project_name="demo",
            task_type="tts",
            resource_ids=["E1U1"],
            script_file=locator,
            user_id="default",
        )
        for locator in ("episode_1.json", "scripts/episode_1.json")
    ]


async def test_empty_tts_observation_does_not_open_the_queue() -> None:
    queue = AsyncMock()

    active = await narration_delivery_tasks.active_tts_resource_ids(
        project_name="demo",
        resource_ids=(),
        script_file="episode_1.json",
        queue=queue,
    )

    assert active == frozenset()
    queue.get_active_tasks_for_resources.assert_not_called()
