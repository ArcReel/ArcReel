import asyncio

from lib.backends.text_backends.base import TextProgress
from lib.generation.text_progress import TaskProgressTable


async def test_progress_survives_calls_and_clears_after_completion():
    table = TaskProgressTable()
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


async def test_throttled_last_delta_reaches_idle_subscriber(monkeypatch):
    loop = asyncio.get_running_loop()
    now = loop.time()
    monkeypatch.setattr(loop, "time", lambda: now)
    published = []
    table = TaskProgressTable()

    def collect(project, payload):
        published.append(payload["characters"])

    table.publish = collect
    with table.track("p", "id", "text_episode_script") as report:
        report(TextProgress("receiving", "m", 1))
        report(TextProgress("receiving", "m", 4))
        report(TextProgress("receiving", "m", 8))
        assert table.get("id") == {"task_id": "id", "seq": 3, "phase": "receiving", "characters": 8, "thinking": False}
        assert published == [1]
        tick = asyncio.Event()
        loop.call_at(now + 2.1, tick.set)
        now += 2.1
        await tick.wait()
        assert published == [1, 8]
