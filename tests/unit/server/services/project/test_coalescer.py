import asyncio

from server.services.project.coalesce import RequestCoalescer


async def test_foreign_loop_computes_on_service_loop():
    owner = asyncio.get_running_loop()
    coalescer = RequestCoalescer(owner)

    async def compute():
        return asyncio.get_running_loop()

    result = await asyncio.to_thread(lambda: asyncio.run(coalescer.run("project", compute)))
    assert result is owner


async def test_cancelled_client_and_failed_round_leave_fresh_waiter_and_retry():
    reads = RequestCoalescer()
    started = asyncio.Event()
    release = asyncio.Event()
    version = 1
    observed = []

    async def compute():
        observed.append(version)
        if len(observed) == 1:
            started.set()
            await release.wait()
            raise ValueError("read failed")
        return version

    abandoned = asyncio.create_task(reads.run("plan", compute))
    await started.wait()
    waiter = asyncio.create_task(reads.run("plan", compute))
    kept = asyncio.create_task(reads.run("plan", compute))
    abandoned.cancel()
    await asyncio.gather(abandoned, return_exceptions=True)
    version = 2
    release.set()
    assert await asyncio.gather(waiter, kept) == [2, 2]
    version = 3
    assert await reads.run("plan", compute) == 3
    assert observed == [1, 2, 3]


async def test_is_closed_tracks_shutdown_and_closed_loop():
    reads = RequestCoalescer(asyncio.get_running_loop())
    assert not reads.is_closed
    await reads.close()
    assert reads.is_closed

    loop = asyncio.new_event_loop()
    bound = RequestCoalescer(loop)
    assert not bound.is_closed
    loop.close()
    assert bound.is_closed
