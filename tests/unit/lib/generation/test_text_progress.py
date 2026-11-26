import asyncio
from unittest.mock import Mock

from lib.backends.text_backends.base import TextGenerationRequest, TextGenerationResult, TextProgress
from lib.backends.text_backends.streaming import TextDelta, execute_stream
from lib.generation.text_progress import TaskProgressTable


async def test_progress_survives_calls_and_clears_after_completion():
    loop = Mock(spec=asyncio.AbstractEventLoop)
    loop.time.return_value = 100.0
    table = TaskProgressTable(scheduler=loop)
    events = []
    table.publish = lambda project, payload: events.append((project, payload))
    with table.track("p", "id", "text_episode_script") as report:
        report(TextProgress("waiting", "m"))
        report(TextProgress("receiving", "m", thinking=True))
        report(TextProgress("receiving", "m", 3))
        report(TextProgress("receiving", "m", 6))
        assert table.get("id") == {"task_id": "id", "seq": 4, "phase": "receiving", "characters": 6, "thinking": False}
        report(TextProgress("waiting", "m"))
        report(TextProgress("retrying", "m", error="RateLimitError", attempt=2))
    assert [event["seq"] for _, event in events] == [1, 2, 3, 5, 6]
    assert table.get("id") is None


async def test_throttled_last_delta_reaches_idle_subscriber():
    loop = Mock(spec=asyncio.AbstractEventLoop)
    loop.time.return_value = 100.0
    published = []
    table = TaskProgressTable(scheduler=loop)

    def collect(project, payload):
        published.append(payload["characters"])

    table.publish = collect
    with table.track("p", "id", "text_episode_script") as report:
        report(TextProgress("receiving", "m", 1))
        report(TextProgress("receiving", "m", 4))
        report(TextProgress("receiving", "m", 8))
        assert table.get("id") == {"task_id": "id", "seq": 3, "phase": "receiving", "characters": 8, "thinking": False}
        assert published == [1]
        deadline, flush = loop.call_at.call_args.args
        assert deadline == 102.0
        loop.time.return_value = deadline
        flush()
        assert published == [1, 8]


async def test_task_counts_answer_characters_across_model_calls():
    table = TaskProgressTable()

    async def driver(request):
        yield TextDelta(request.prompt)
        yield TextGenerationResult(request.prompt, "provider", "model")

    with table.track("demo", "id", "text_episode_plan"):
        for answer, total in [("first answer", 12), ("next", 16)]:
            await execute_stream(
                TextGenerationRequest(answer), driver, provider="provider", protocol="test", model="model"
            )
            assert table.get("id") == {
                "phase": "receiving",
                "task_id": "id",
                "seq": 2 if total == 12 else 4,
                "characters": total,
                "thinking": False,
            }
