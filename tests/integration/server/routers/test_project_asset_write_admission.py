import httpx
import pytest
from fastapi import FastAPI

from lib.generation.generation_queue import GenerationQueue
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import characters, derivative_uploads, files, generate, products, versions
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.factories import wav_bytes
from tests.fakes import bind_session_factory, use_generation_queue, use_project_manager
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool
from tests.integration.server.derivative_sheet_support import solid_png_bytes


@pytest.fixture
async def asset_writes(tmp_path, monkeypatch, file_db_factory):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo")
    manager.add_character("demo", "Alice", "hero")
    manager.add_character("demo", "Bob", "friend")
    manager.add_product("demo", "Pack", "a sample product")
    manager.update_asset_entry(
        "character", "demo", "Alice", lambda entry: entry.update(derivatives={"Armor": {"description": "metal"}})
    )
    use_project_manager(monkeypatch, manager)
    bind_session_factory(monkeypatch, file_db_factory)
    monkeypatch.setattr("server.tool_runtime.async_session_factory", file_db_factory)
    queue = GenerationQueue(session_factory=file_db_factory, project_manager=manager)
    use_generation_queue(monkeypatch, queue)
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    for router in (characters, derivative_uploads, files, generate, products, versions):
        app.include_router(router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/projects/demo/"
    ) as client:
        yield client, manager, queue


@pytest.mark.parametrize("upload", ["character", "character?name=Alice", "character_ref", "character_ref?name=Alice"])
async def test_upload_does_not_overwrite_a_busy_character(asset_writes, upload):
    client, manager, queue = asset_writes
    initial = await client.post(f"upload/{upload}", files={"file": ("Alice.png", solid_png_bytes((15, 20, 25)))})
    assert initial.status_code == 200, initial.text
    target = manager.get_project_path("demo") / initial.json()["path"]
    before = target.read_bytes(), manager.load_project("demo")
    await queue.enqueue_task(
        project_name="demo", task_type="character", resource_id="Alice", media_type="image", provider_id="test"
    )

    response = await client.post(f"upload/{upload}", files={"file": ("Alice.png", solid_png_bytes((25, 20, 15)))})

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "asset_resource_busy"
    assert (target.read_bytes(), manager.load_project("demo")) == before


def _assert_busy(response):
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "asset_resource_busy"


async def _enqueue(queue, task_type="character", name="Alice"):
    return await queue.enqueue_task(
        project_name="demo",
        task_type=task_type,
        resource_id=name,
        media_type="audio" if task_type == "voice_sample" else "image",
        provider_id="test",
    )


async def _upload_image(client, route, color):
    return await client.post(route, files={"file": ("image.png", solid_png_bytes(color), "image/png")})


@pytest.mark.parametrize(
    ("upload_path", "version_path"),
    [
        ("upload/character?name=Alice", "versions/characters/Alice"),
        ("characters/Alice/derivatives/Armor/upload", "versions/character-derivative/Alice/Armor"),
    ],
)
async def test_busy_version_restore_preserves_current_bytes_and_history(asset_writes, upload_path, version_path):
    client, manager, queue = asset_writes
    first = await _upload_image(client, upload_path, (1, 2, 3))
    current = await _upload_image(client, upload_path, (7, 8, 9))
    assert first.status_code == current.status_code == 200
    path = manager.get_project_path("demo") / current.json()["path"]
    before = path.read_bytes(), manager.load_project("demo"), (await client.get(version_path)).json()
    await _enqueue(queue)

    _assert_busy(await client.post(f"{version_path}/restore/1"))

    assert (path.read_bytes(), manager.load_project("demo"), (await client.get(version_path)).json()) == before


async def test_busy_derivative_allows_description_but_rejects_image_rename_and_delete(asset_writes):
    client, manager, queue = asset_writes
    route = "characters/Alice/derivatives/Armor"
    upload = await _upload_image(client, f"{route}/upload", (1, 2, 3))
    assert upload.status_code == 200, upload.text
    path = manager.get_project_path("demo") / upload.json()["path"]
    before_image = path.read_bytes()
    await _enqueue(queue, "character_derivative", "Alice/Armor")

    updated = await client.patch(route, json={"description": "bronze"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["character"]["derivatives"]["Armor"]["description"] == "bronze"
    before = manager.load_project("demo"), (await client.get("versions/character-derivative/Alice/Armor")).json()
    _assert_busy(await _upload_image(client, f"{route}/upload", (3, 2, 1)))
    _assert_busy(await client.post(f"{route}/rename", json={"new_name": "Coat"}))
    _assert_busy(await client.delete(route))

    assert path.read_bytes() == before_image
    assert (
        manager.load_project("demo"),
        (await client.get("versions/character-derivative/Alice/Armor")).json(),
    ) == before


@pytest.mark.parametrize(
    ("method", "tail", "body", "busy_name"),
    [
        ("DELETE", "", None, "Alice"),
        ("POST", "/rename", {"new_name": "Carol"}, "Alice"),
        ("POST", "/merge", {"target": "Bob"}, "Alice"),
        ("POST", "/merge", {"target": " Bob "}, "Bob"),
    ],
)
async def test_web_identity_previews_work_but_busy_writes_leave_assets_intact(
    asset_writes, method, tail, body, busy_name
):
    client, manager, queue = asset_writes
    left = await _upload_image(client, "upload/character?name=Alice", (1, 2, 3))
    right = await _upload_image(client, "upload/character?name=Bob", (4, 5, 6))
    assert left.status_code == right.status_code == 200
    paths = [manager.get_project_path("demo") / upload.json()["path"] for upload in (left, right)]
    histories = ("versions/characters/Alice", "versions/characters/Bob")
    before = (
        manager.load_project("demo"),
        [path.read_bytes() for path in paths],
        [(await client.get(url)).json() for url in histories],
    )
    await _enqueue(queue, "voice_sample", busy_name)
    route = f"characters/Alice{tail}"

    if body is None:
        preview = await client.delete(f"{route}?dry_run=true")
    else:
        preview = await client.post(route, json={**body, "dry_run": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] is True

    _assert_busy(await client.request(method, route, json=body))
    assert (
        manager.load_project("demo"),
        [path.read_bytes() for path in paths],
        [(await client.get(url)).json() for url in histories],
    ) == before


async def test_agent_identity_writes_share_guard_and_text_updates_pass(asset_writes):
    client, manager, queue = asset_writes
    upload = await _upload_image(client, "upload/character?name=Alice", (1, 2, 3))
    assert upload.status_code == 200, upload.text
    sheet = manager.get_project_path("demo") / upload.json()["path"]
    original = sheet.read_bytes()
    await _enqueue(queue)
    harness = ToolHarness("demo", manager.data_root, manager, queue=queue)
    rename = {"table": "characters", "old_name": "Alice", "new_name": "Carol"}
    merge = {"table": "characters", "source": "Bob", "target": "Alice"}
    before = manager.load_project("demo")

    preview = await run_declared_tool("merge_asset", harness, {**merge, "dry_run": True})
    assert preview.problem is None, preview.problem
    for tool, arguments in (("rename_asset", rename), ("merge_asset", merge)):
        outcome = await run_declared_tool(tool, harness, arguments)
        assert outcome.problem is not None
        assert outcome.problem.code == "asset_resource_busy"
    assert manager.load_project("demo") == before
    assert sheet.read_bytes() == original

    patched = await run_declared_tool(
        "patch_project", harness, {"table": "characters", "entries": {"Alice": {"description": "agent update"}}}
    )
    assert patched.problem is None, patched.problem
    assert manager.load_project("demo")["characters"]["Alice"]["description"] == "agent update"


@pytest.mark.parametrize(("active", "expected"), [("character", 200), ("voice_sample", 409)])
async def test_audio_upload_delete_and_confirmation_follow_voice_occupancy(asset_writes, active, expected):
    client, manager, queue = asset_writes
    wav = wav_bytes(3)
    upload = "upload/character_audio_ref?name=Alice"
    initial = await client.post(upload, files={"file": ("Alice.wav", wav, "audio/wav")})
    assert initial.status_code == 200, initial.text
    project_dir = manager.get_project_path("demo")
    path = project_dir / initial.json()["path"]
    original = path.read_bytes()
    previous = await _enqueue(queue, "voice_sample")
    claimed = await queue.claim_next_task("audio")
    assert claimed is not None
    assert claimed["task_id"] == previous["task_id"]
    sample_rel = "audio/voice_sample__Alice.wav"
    sample = wav_bytes(3, tone_hz=550)
    (project_dir / sample_rel).write_bytes(sample)
    assert await queue.mark_task_succeeded(previous["task_id"], {"file_path": sample_rel}) == 1
    await _enqueue(queue, active)

    before = manager.load_project("demo")
    uploaded = await client.post(upload, files={"file": ("Alice.wav", sample, "audio/wav")})
    assert uploaded.status_code == expected, uploaded.text
    deleted = await client.delete("characters/Alice/reference-audio")
    assert deleted.status_code == expected, deleted.text
    if expected == 200:
        assert not path.exists()
    confirmed = await client.post("characters/Alice/voice-sample/confirm", json={"task_id": previous["task_id"]})
    assert confirmed.status_code == expected, confirmed.text

    if expected == 409:
        for response in (uploaded, deleted, confirmed):
            _assert_busy(response)
        assert manager.load_project("demo") == before
        assert path.read_bytes() == original
    else:
        assert path.read_bytes() == sample


@pytest.mark.parametrize("active", ["character", "voice_sample"])
async def test_character_update_checks_changed_media_and_allows_text_and_same_values(asset_writes, active):
    client, manager, queue = asset_writes
    sheet = await _upload_image(client, "upload/character?name=Alice", (1, 2, 3))
    reference = await _upload_image(client, "upload/character_ref?name=Alice", (4, 5, 6))
    audio = await client.post(
        "upload/character_audio_ref?name=Alice", files={"file": ("Alice.wav", wav_bytes(3), "audio/wav")}
    )
    assert sheet.status_code == reference.status_code == audio.status_code == 200
    paths = [manager.get_project_path("demo") / upload.json()["path"] for upload in (sheet, reference, audio)]
    original = [path.read_bytes() for path in paths]
    route = "characters/Alice"
    await _enqueue(queue, active)
    before = manager.load_project("demo")
    entry = before["characters"]["Alice"]

    for fields in (
        ({"character_sheet": ""}, {"reference_image": ""}) if active == "character" else ({"reference_audio": ""},)
    ):
        _assert_busy(await client.patch(route, json=fields))
    _assert_busy(await client.patch(route, json={"character_sheet": "", "reference_audio": ""}))
    assert manager.load_project("demo") == before
    assert [path.read_bytes() for path in paths] == original

    patched = await client.patch(
        route,
        json={
            "description": "new description",
            "aliases": ["Heroine"],
            "voice_style": "quiet",
            "character_sheet": entry["character_sheet"],
            "reference_image": entry["reference_image"],
            "reference_audio": entry["reference_audio"],
            "voice_notice_dismissed_at": entry["voice_updated_at"],
        },
    )
    assert patched.status_code == 200, patched.text
    updated = patched.json()["character"]
    assert (updated["description"], updated["aliases"], updated["voice_style"]) == (
        "new description",
        ["Heroine"],
        "quiet",
    )
    assert updated["voice_updated_at"] == updated["voice_notice_dismissed_at"] == entry["voice_updated_at"]
    stale = await client.patch(route, json={"voice_notice_dismissed_at": "old"})
    assert stale.status_code == 422, stale.text

    allowed_field = "reference_audio" if active == "character" else "character_sheet"
    unoccupied = await client.patch(route, json={allowed_field: ""})
    assert unoccupied.status_code == 200, unoccupied.text
    if active == "character":
        assert unoccupied.json()["character"]["voice_updated_at"] != entry["voice_updated_at"]


async def test_product_reference_upload_can_append_during_generation(asset_writes):
    client, manager, queue = asset_writes
    route = "upload/product_ref?name=Pack"
    old = await _upload_image(client, route, (1, 2, 3))
    assert old.status_code == 200, old.text
    old_path = manager.get_project_path("demo") / old.json()["path"]
    original = old_path.read_bytes()

    await _enqueue(queue, "product", "Pack")
    _assert_busy(await client.patch("products/Pack", json={"reference_images": []}))
    _assert_busy(await _upload_image(client, "upload/product?name=Pack", (4, 5, 6)))
    new = await _upload_image(client, route, (4, 5, 6))
    assert new.status_code == 200, new.text
    assert old_path.read_bytes() == original
    updated = await client.patch("products/Pack", json={"brand": "Example", "selling_points": ["compact"]})
    assert updated.status_code == 200, updated.text
    entry = updated.json()["product"]
    assert (entry["brand"], entry["selling_points"]) == ("Example", ["compact"])
    assert entry["reference_images"] == [old.json()["path"], new.json()["path"]]
