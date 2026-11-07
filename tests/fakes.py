"""Shared fake / stub objects for tests.

Only objects used across multiple test files belong here.
Single-file fakes stay in their respective test modules.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
from collections.abc import AsyncIterator, Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from instructor.core import InstructorRetryException

if TYPE_CHECKING:
    from lib.artifacts.version_manager import PaidVersionCommit
    from lib.generation.media_generator import MediaGenerator


_NO_SDK_MESSAGES: tuple[dict[str, Any], ...] = ()
# close_stream injects this termination sentinel; once read, the raw-frame stream ends, including subsequent reads.
_STREAM_EOF = object()

#: Substitute artifact bytes with real headers. Retrieval verifies containers by headers (see
#: ``lib.custom_provider.comfyui.artifacts.container_matches``); bare placeholder bytes fail container matching.
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00mp4-bytes"
PNG_BYTES = b"\x89PNG\r\n\x1a\npng-bytes"


@contextmanager
def captured_vertex_credentials() -> Generator[MagicMock]:
    """Replace service-account private-key parsing, retaining credential paths and scopes for assembly assertions."""
    with patch("google.oauth2.service_account.Credentials.from_service_account_file", autospec=True) as load:
        yield load


@contextmanager
def failing_manifest_replace(error: BaseException) -> Generator[None]:
    """Make manifest conditional replacement (the migration-registration commit point) raise *error*, simulating write failure or process termination at commit."""

    from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter

    with patch.object(ProjectArtifactManifestAdapter, "replace_entries_if_matches_atomically", side_effect=error):
        yield


async def write_backend_output(path: Path, data: bytes) -> None:
    """Write substitute artifacts in threads, matching production backends without blocking the event loop."""

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    await asyncio.to_thread(_write)


async def empty_sdk_response_stream() -> AsyncIterator[dict[str, Any]]:
    """An SDK response stream that ends without producing any messages.

    Return this function's result for empty fake-client streams; directly using ``if False: yield`` would leave
    a statically unreachable branch.
    """
    for message in _NO_SDK_MESSAGES:
        yield message


class FakeProjectAssetMutationMixin:
    """Share production-shaped asset mutation contracts across router fakes."""

    expected_delete_asset_table: str | None = None

    def load_project(self, project_name: str) -> dict[str, Any]:
        raise NotImplementedError

    def update_project(self, project_name: str, mutate_fn: Callable[[dict], None]) -> Any:
        raise NotImplementedError

    def get_project_path(self, project_name: str) -> Path:
        # No project directory by default: use a nonexistent location, so new-asset project-trash lookup is empty.
        return Path("/nonexistent-arcreel-fake-projects") / project_name

    def update_asset_entry(
        self,
        asset_type: str,
        project_name: str,
        name: str,
        mutate_fn: Callable[[dict], None],
        *,
        on_commit: Callable[[Path], None] | None = None,
        formal_paths: Sequence[Path] = (),
    ) -> dict[str, Any]:
        from lib.project.asset_types import ASSET_SPECS, resolve_asset_key

        spec = ASSET_SPECS[asset_type]
        result: dict[str, Any] = {}

        def _mutate(project: dict) -> None:
            bucket = project.get(spec.bucket_key) or {}
            key = resolve_asset_key(bucket, name)
            if key is None:
                raise KeyError(name)
            entry = bucket[key]
            mutate_fn(entry)
            result.update(entry)

        self.update_project(project_name, _mutate)
        if on_commit is not None:
            # Production supplies project.json paths, not project names; match that shape
            # so on_commit consumers receive paths instead of relative names.
            on_commit(self.get_project_path(project_name) / "project.json")
        return result

    def rename_asset_derivative(
        self,
        asset_type: str,
        project_name: str,
        entry_name: str,
        old_name: str,
        new_name: str,
    ) -> dict[str, Any]:
        """Project-side derivative rename results; ProjectManager tests cover cascading script-reference rewrites."""
        from lib.project.asset_derivatives import find_derivative_name
        from lib.project.asset_rename import AssetRenameConflictError, AssetRenameNotFoundError
        from lib.project.asset_types import (
            DERIVATIVES_FIELD,
            rekey_equivalent_entries,
            resolve_asset_key,
            validate_asset_name,
        )

        clean = validate_asset_name(new_name)

        def _mutate(entry: dict) -> None:
            table = entry.get(DERIVATIVES_FIELD)
            if not isinstance(table, dict):
                raise AssetRenameNotFoundError(old_name)
            key = resolve_asset_key(table, old_name)
            if key is None:
                raise AssetRenameNotFoundError(old_name)
            taken = find_derivative_name(table, clean, exclude=key)
            if taken is not None:
                raise AssetRenameConflictError(taken)
            rekey_equivalent_entries(table, key, clean)

        return self.update_asset_entry(asset_type, project_name, entry_name, _mutate)

    def delete_asset(self, project_name: str, table: str, name: str) -> dict[str, Any]:
        from lib.project.asset_types import resolve_asset_key

        if self.expected_delete_asset_table is not None:
            assert table == self.expected_delete_asset_table
        project = self.load_project(project_name)
        bucket = project.get(table) or {}
        key = resolve_asset_key(bucket, name)
        if key is None:
            raise KeyError(name)
        del bucket[key]
        return project


def persist_fake_script(project_path: Path, script_file: object, script: object) -> None:
    """Mirror an in-memory fake script through the production scripts directory."""

    normalized = str(script_file).replace("\\", "/").removeprefix("scripts/")
    target = project_path / "scripts" / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def write_generation_targets(
    project_path: Path, *, segments: tuple[str, ...] = (), video_units: tuple[str, ...] = ()
) -> None:
    """Write minimal project data containing these storyboard entries and video units in episode 1.

    Queue tasks recheck target resources before artifact commit; removed targets send artifacts to project trash.
    """
    (project_path / "project.json").write_text(
        json.dumps({"episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}]}), encoding="utf-8"
    )
    script = {
        "segments": [{"segment_id": item_id} for item_id in segments],
        "video_units": [{"unit_id": item_id} for item_id in video_units],
    }
    persist_fake_script(project_path, "episode_1.json", script)


def select_formal_video(
    generator: MediaGenerator,
    *,
    resource_type: str = "reference_videos",
    resource_id: str = "E1U1",
    prompt: str = "",
) -> Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit]:
    """Build the minimal paid-video formal commit callback used by generator tests."""

    def _commit(
        staged_file: Path,
        current_file: Path,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
    ) -> PaidVersionCommit:
        return generator.versions.commit_staged_paid_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            staged_file=staged_file,
            current_file=current_file,
            select_current=True,
            duration_seconds=duration_seconds,
            **version_metadata,
        )

    return _commit


class FakeTextGenerator:
    """Text-model substitute: replay preset reply text in order and record every received request.

    Install through ``use_text_generator(monkeypatch, fake.create)``; exception replies raise during
    ``generate`` to simulate provider failures.
    """

    #: Same name as ``TextGenerator.max_output_tokens``: effective limit without registered maximum output length.
    max_output_tokens = 64000
    #: Same name as ``TextGenerator.model``: model name callers write to metadata.
    model = "fake-text"

    def __init__(self, *responses: str | BaseException):
        self._responses = list(responses)
        self.requests: list[Any] = []
        #: Each call's ``require_complete``, corresponding one-to-one with ``requests``.
        self.require_complete: list[bool] = []

    async def create(self, _task_type: object, _project_name: str | None = None, **_kwargs: Any) -> FakeTextGenerator:
        return self

    async def generate(self, request: Any, project_name: str | None = None, *, require_complete: bool = False) -> Any:
        from lib.backends.text_backends.base import TextGenerationResult

        self.requests.append(request)
        self.require_complete.append(require_complete)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return TextGenerationResult(text=response, provider="fake", model="fake-text")


def assistant_frame(
    *content: dict[str, Any],
    uuid: str | None = None,
    parent_tool_use_id: str | None = None,
    session_id: str = "default",
    model: str = "fake-model",
    message_id: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Raw CLI assistant frame; ``content`` holds content blocks."""
    message: dict[str, Any] = {"role": "assistant", "content": list(content), "model": model}
    if message_id is not None:
        message["id"] = message_id
    if usage is not None:
        message["usage"] = usage
    return {
        "type": "assistant",
        "message": message,
        "parent_tool_use_id": parent_tool_use_id,
        "session_id": session_id,
        "uuid": uuid,
    }


def system_frame(subtype: str, **fields: Any) -> dict[str, Any]:
    """Raw CLI system frame with flat top-level fields; SDK parsing places the whole frame in ``SystemMessage.data``."""
    return {"type": "system", "subtype": subtype, **fields}


def session_state_message(state: str) -> dict[str, Any]:
    """The CLI ``session_state_changed`` frame shape delivered to sessions after SDK parsing/serialization (state inside ``data``).

    Feed directly to the read callback from ``SessionManager._make_actor_message_callback``; to emit through the fake, use
    ``system_frame("session_state_changed", state=...)``.
    """
    return {
        "type": "system",
        "subtype": "session_state_changed",
        "data": system_frame("session_state_changed", state=state),
    }


def stream_event_frame(
    event: dict[str, Any],
    *,
    uuid: str = "stream-event",
    session_id: str = "default",
    parent_tool_use_id: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "stream_event",
        "uuid": uuid,
        "session_id": session_id,
        "event": event,
        "parent_tool_use_id": parent_tool_use_id,
    }


def result_frame(
    subtype: str = "success",
    *,
    is_error: bool = False,
    session_id: str = "default",
    uuid: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Raw CLI result frame; ``fields`` adds ``stop_reason``, ``terminal_reason``, ``usage``, and other fields."""
    return {
        "type": "result",
        "subtype": subtype,
        "duration_ms": 0,
        "duration_api_ms": 0,
        "is_error": is_error,
        "num_turns": 1,
        "session_id": session_id,
        "uuid": uuid,
        **fields,
    }


def command_lifecycle_frame(command_uuid: str, state: str, *, session_id: str = "default") -> dict[str, Any]:
    """CLI reports the disposition of a uuid-tagged submitted user message (``queued`` / ``started`` / ``completed`` / ``cancelled``, etc.)."""
    return {
        "type": "command_lifecycle",
        "command_uuid": command_uuid,
        "state": state,
        "uuid": f"lifecycle-{command_uuid}-{state}",
        "session_id": session_id,
    }


ScriptedFrame = dict[str, Any] | Callable[["FakeSDKClient"], dict[str, Any]]
"""Frames emitted by the substitute, or functions building frames from its state at emission time (to reference message uuids submitted by ArcReel)."""


def replay_frame(index: int = -1, *, session_id: str = "default") -> Callable[[FakeSDKClient], dict[str, Any]]:
    """CLI replay of submitted message ``index``, preserving its original uuid."""

    def _build(client: FakeSDKClient) -> dict[str, Any]:
        sent = client.sent_messages[index]
        return {
            "type": "user",
            "message": {"role": "user", "content": sent["message"]["content"]},
            "parent_tool_use_id": None,
            "session_id": session_id,
            "uuid": sent["uuid"],
            "isReplay": True,
        }

    return _build


_ControlResponse = dict[str, Any] | BaseException | Callable[[dict[str, Any]], dict[str, Any]]


class _FakeQuery:
    """``ClaudeSDKClient._query`` substitute: raw-frame stream and control-request entry."""

    def __init__(self, client: FakeSDKClient):
        self._client = client

    async def receive_messages(self) -> AsyncIterator[dict[str, Any]]:
        client = self._client
        client._record("receive_messages")
        while not client._stream_closed:
            frame = await client._frames.get()
            if frame is _STREAM_EOF:
                client._stream_closed = True
                return
            yield frame

    async def _send_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        client = self._client
        client._record("control_request")
        client.control_requests.append(request)
        subtype = request.get("subtype")
        if subtype not in client._control_responses:
            raise AssertionError(f"测试没有为控制请求 {subtype!r} 指定应答")
        response = client._control_responses[subtype]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(request)
        return response


class FakeSDKClient:
    """Fake Claude Agent SDK client for SessionActor / SessionManager tests.

    Emit raw frames as the CLI writes to stdout, constructed with ``assistant_frame`` and peers, through
    ``_query.receive_messages()`` to actors for real SDK parsing. Do not simulate CLI queue logic.

    - `async with`: `__aenter__` records connect current_task; `__aexit__` records disconnect.
    - `method_tasks`: dict[str, list[asyncio.Task]] records the task calling each method.
    - `frames`: initial raw-frame round emitted after first `query()`; later `push_frame` emits scripted frames. Frames can be
      functions such as ``replay_frame()`` that generate frames from fake state when emitted.
    - `sent_messages`: uuid-tagged user frames submitted by ArcReel; `sent_queries` contains their message content.
    - A result frame ends a round; without one, the round remains in flight.
    - `interrupt_frame`: frame emitted by `interrupt()`, usually that round's result.
    - `control_responses`: subtype-indexed responses, as dicts, exceptions to raise, or request-to-response functions
      (which may `push_frame`); emitted requests enter `control_requests`.
    - `connect_error`: exception raised in `__aenter__`, simulating connection failure.
    """

    def __init__(
        self,
        frames: list[ScriptedFrame] | None = None,
        *,
        interrupt_frame: dict[str, Any] | None = None,
        control_responses: Mapping[str, _ControlResponse] | None = None,
        connect_error: Exception | None = None,
    ):
        self._initial_frames = list(frames) if frames else []
        self._interrupt_frame = interrupt_frame
        self._control_responses: dict[str, _ControlResponse] = dict(control_responses or {})
        self._connect_error = connect_error
        self._frames: asyncio.Queue[Any] = asyncio.Queue()
        self._query = _FakeQuery(self)
        self.method_tasks: dict[str, list[asyncio.Task]] = {}
        self.sent_queries: list = []
        self.sent_messages: list[dict[str, Any]] = []
        self.control_requests: list[dict[str, Any]] = []
        self.interrupted = False
        self.disconnected = False
        self._stream_closed = False

    def _record(self, method: str) -> None:
        self.method_tasks.setdefault(method, []).append(asyncio.current_task())

    async def __aenter__(self):
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._record("disconnect")
        self.disconnected = True
        return False

    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        if isinstance(prompt, str):
            self.sent_queries.append(prompt)
        else:
            async for message in prompt:
                self.sent_messages.append(message)
                self.sent_queries.append(message["message"]["content"])
        # Like the real CLI, initial replies appear only after receiving prompts.
        for frame in self._initial_frames:
            await self._frames.put(frame(self) if callable(frame) else frame)
        self._initial_frames.clear()

    async def interrupt(self) -> None:
        self._record("interrupt")
        self.interrupted = True
        if self._interrupt_frame is not None:
            await self._frames.put(self._interrupt_frame)

    def push_frame(self, frame: ScriptedFrame) -> None:
        """Test helper: make the running CLI emit one raw frame."""
        self._frames.put_nowait(frame(self) if callable(frame) else frame)

    def respond_control(self, subtype: str, response: _ControlResponse) -> None:
        """Test helper: configure subsequent control responses for this subtype."""
        self._control_responses[subtype] = response

    def close_stream(self) -> None:
        """Test helper: simulate CLI exit, ending the raw stream after already-emitted frames are consumed."""
        self._frames.put_nowait(_STREAM_EOF)

    # Backward compatibility: retain old signatures for `await client.connect()` / `await client.disconnect()`.
    async def connect(self) -> None:
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def disconnect(self) -> None:
        self._record("disconnect")
        self.disconnected = True


async def build_managed_with_actor(
    *,
    session_id: str = "s1",
    project_name: str = "demo",
    status: str = "idle",
    frames: list[dict] | None = None,
    on_message_hook=None,
):
    """Test helper: create SessionActor + ManagedSession around FakeSDKClient and start the actor.

    Return (managed, actor, client). Clean up with `await managed.send_disconnect()` after testing,
    or let callers manage the lifecycle themselves.
    """
    from contextlib import asynccontextmanager

    from server.agent_runtime.message_serialization import message_to_dict
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession

    client = FakeSDKClient(frames=frames)

    @asynccontextmanager
    async def _factory_cm():
        async with client as c:
            yield c

    managed_ref: list = [None]

    def _on_message(raw_msg):
        m = managed_ref[0]
        if m is None:
            return
        # Match production callbacks: actors deliver SDK messages; sessions process serialized dicts.
        msg = message_to_dict(raw_msg)
        if on_message_hook is not None:
            on_message_hook(m, msg)
        else:
            m._on_actor_message(msg)

    actor = SessionActor(client_factory=_factory_cm, on_message=_on_message)
    managed = ManagedSession(
        session_id=session_id,
        actor=actor,
        status=status,
        project_name=project_name,
    )
    managed_ref[0] = managed
    await actor.start()
    return managed, actor, client


def fake_reference_request_facts(
    *,
    durations: tuple[int, ...] = (),
    failures: Mapping[str, Any] | None = None,
    **overrides: Any,
):
    """Video-request fact lookup for reference-route projection: return constructed results by bucket.

    ``failures`` maps buckets to ``VideoRequestFactsFailure`` objects returned unchanged; other buckets build successful
    facts from ``durations`` / ``overrides`` via ``tests.factories.make_video_request_facts``.
    """

    from tests.factories import make_video_request_facts

    async def lookup(generation_type):
        if failures is not None and generation_type in failures:
            return failures[generation_type]
        fields: dict[str, Any] = {
            "route": "reference_video",
            "generation_type": generation_type,
            "provider_id": "fake",
            "model_id": "fake-model",
            "resolution": "1080p",
            "supported_durations": durations,
            "allowed_durations": durations,
            "max_reference_images": 9,
            "audio_switch_controllable": True,
        }
        fields.update(overrides)
        return make_video_request_facts(**fields)

    return lookup


def fake_reference_request_projector(
    *,
    durations: tuple[int, ...] | None = None,
    provider_id: str = "fake",
    model_id: str = "fake-model",
    max_reference_images: int | None = 9,
    text_to_video: bool = True,
    request_facts=None,
):
    """Build an async test entry using real asset hydration/projection, replacing only video-request facts.

    Supplied ``request_facts`` becomes bucket lookup unchanged (see :func:`fake_reference_request_facts`);
    otherwise construct it from remaining arguments.
    """

    from lib.script.reference_video.request_projection import (
        FilesystemReferenceAssets,
        ReferenceRequestOptions,
        ReferenceUnitRequestProjection,
        ReferenceUnitRequestProjector,
        resolve_reference_assets,
    )

    if request_facts is not None:
        if (
            durations is not None
            or provider_id != "fake"
            or model_id != "fake-model"
            or max_reference_images != 9
            or text_to_video is not True
        ):
            raise ValueError("request_facts cannot be combined with facts construction fields")
        facts_lookup = request_facts
    else:
        if durations is None:
            raise ValueError("durations are required when request_facts are not supplied")
        facts_lookup = fake_reference_request_facts(
            durations=durations,
            provider_id=provider_id,
            model_id=model_id,
            max_reference_images=max_reference_images,
            text_to_video=text_to_video,
        )

    async def _project(
        *,
        project: dict,
        script: dict,
        unit: dict,
        project_path: Path,
        options: ReferenceRequestOptions | None = None,
        **_kwargs: object,
    ) -> ReferenceUnitRequestProjection:
        return await ReferenceUnitRequestProjector(
            facts_lookup,
            FilesystemReferenceAssets(project_path),
        ).project_current(
            project=project,
            script=script,
            unit=unit,
            resolved_assets=resolve_reference_assets(project, project_path, unit),
            options=options,
        )

    return _project


class FakeConfigResolver:
    """Handwritten capability-resolver seam substitute: answer bucketed video capabilities without configuration DB access.

    Inject into Agent-tool ``ToolHarness`` and production entries accepting ``config_resolver`` (``MediaGenerator``,
    ``project_video_caps``, and other capability-dict readers), rather than replacing those whole readers. Their soft fallbacks and
    derived voice settings are the behaviors to protect. This class does not implement execution-model resolution or per-model capability composition;
    video-request-fact consumer tests use ``tests/conftest.py`` ``set_video_request_facts`` to supply results directly.

    ``by_generation_type`` supports bucket-specific branches (reference-to-video units without references select i2v); keys are
    ``VideoGenerationType`` literals, values are field overlays on base capabilities. ``error`` / ``generate_audio_error``
    exercise soft fallback without more patches. ``image_resolution`` supplies image tiers for grid gates.
    """

    def __init__(
        self,
        *,
        supported_durations: tuple[int, ...] | list[int] = (4, 6, 8),
        default_duration: int | None = 4,
        provider_id: str = "fake",
        model: str = "fake-video",
        max_reference_images: int | None = 3,
        max_reference_audio_count: int = 0,
        reference_audio_per_image: bool = False,
        generate_audio: bool = True,
        requested_generate_audio: bool = True,
        voice_consistency: str = "soft",
        by_generation_type: Mapping[str, Mapping[str, Any]] | None = None,
        generation_type_errors: Mapping[str, BaseException] | None = None,
        error: BaseException | None = None,
        generate_audio_error: BaseException | None = None,
        image_backend: tuple[str, str] = ("fake", "fake-image"),
        image_backend_error: BaseException | None = None,
        reference_payload_limits: tuple[int, int] | None = None,
        image_resolution: str = "1080p",
        **extra: Any,
    ) -> None:
        self._base: dict[str, Any] = {
            "provider_id": provider_id,
            "model": model,
            "supported_durations": list(supported_durations),
            "max_duration": max(supported_durations) if supported_durations else 0,
            "max_reference_images": max_reference_images,
            "max_reference_audio_count": max_reference_audio_count,
            "reference_audio_per_image": reference_audio_per_image,
            "generate_audio": generate_audio,
            "requested_generate_audio": requested_generate_audio,
            "voice_consistency": voice_consistency,
            "source": "registry",
            "default_duration": default_duration,
            **extra,
        }
        self._by_generation_type = {key: dict(value) for key, value in (by_generation_type or {}).items()}
        self._generation_type_errors = dict(generation_type_errors or {})
        self._error = error
        self._generate_audio_error = generate_audio_error
        self._image_backend = image_backend
        self._image_backend_error = image_backend_error
        self._reference_payload_limits = reference_payload_limits
        self._image_resolution = image_resolution
        self.generation_type_calls: list[str | None] = []
        self.project_payloads: list[dict[str, Any]] = []
        self.image_generation_type_calls: list[str | None] = []
        self.generate_audio_calls: list[dict[str, Any] | None] = []
        self.generate_audio_project_names: list[str | None] = []
        self.reference_limits_calls: list[str | None] = []

    def caps_for(self, generation_type: str | None = None) -> dict[str, Any]:
        """The bucket's capability dict, matching production shape for direct expected-value comparisons."""
        caps = dict(self._base)
        caps.update(self._by_generation_type.get(generation_type or "", {}))
        durations = caps.get("supported_durations") or []
        caps["max_duration"] = max(durations) if durations else 0
        return caps

    async def video_capabilities_for_project(
        self,
        project: dict[str, Any],
        *,
        generation_type: str | None = None,
    ) -> dict[str, Any]:
        self.project_payloads.append(project)
        return self._resolve(generation_type)

    async def resolve_resolution(self, project: dict[str, Any], provider_id: str, model_id: str) -> str:
        del project, provider_id, model_id
        return self._image_resolution

    async def resolve_image_backend(
        self,
        project: dict[str, Any] | None,
        payload: dict[str, Any] | None = None,
        *,
        generation_type: str | None = None,
    ) -> Any:
        """Resolve image providers; ``image_backend_error`` exercises unavailable project-slot provider resolution."""
        from lib.config.resolver import ProviderModel

        del project, payload
        self.image_generation_type_calls.append(generation_type)
        if self._image_backend_error is not None:
            raise self._image_backend_error
        return ProviderModel(*self._image_backend)

    async def video_generate_audio_for_project(self, project: dict[str, Any] | None) -> bool:
        self.generate_audio_calls.append(project)
        if self._generate_audio_error is not None:
            raise self._generate_audio_error
        return bool(self._base["requested_generate_audio"])

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """Production-named read point by project name, sharing configuration with ``video_generate_audio_for_project``.

        Record arguments separately at both read points (project name here, project dict in the other);
        keep record lists distinct so one path does not contaminate another's equality assertions.
        """
        self.generate_audio_project_names.append(project_name)
        if self._generate_audio_error is not None:
            raise self._generate_audio_error
        return bool(self._base["requested_generate_audio"])

    async def reference_payload_limits(self, provider_id: str | None = None) -> tuple[int, int]:
        """Reference-image payload limits (total, per-image); absent configuration uses the same conservative service defaults as production."""
        self.reference_limits_calls.append(provider_id)
        if self._reference_payload_limits is not None:
            return self._reference_payload_limits
        from lib.config.service import (
            DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        return DEFAULT_REFERENCE_TOTAL_MAX_BYTES, DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    def _resolve(self, generation_type: str | None) -> dict[str, Any]:
        self.generation_type_calls.append(generation_type)
        if self._error is not None:
            raise self._error
        bucket_error = self._generation_type_errors.get(generation_type or "")
        if bucket_error is not None:
            raise bucket_error
        return self.caps_for(generation_type)


def instructor_api_call_exhausted(cause: Exception) -> InstructorRetryException:
    """Construct API-call-failure Instructor exceptions for structured-output fallback-chain tests.

    Exceptions during API calls (rejected parameters, transient 5xx, connection errors) interrupt within-tier retries and wrap as
    ``InstructorRetryException``, retaining the original only as ``__cause__``. Fallback criteria must recognize exactly that
    shape; bare API exception stubs would test nonexistent production paths. Empty ``failed_attempts`` means no parsing attempt
    occurred in this tier. Test modules construct mixed shapes with prior parsing failures followed by API failure themselves.
    ``TestInstructorExceptionShape`` pins this structure against real Instructor behavior.
    """
    exc = InstructorRetryException(
        str(cause),
        last_completion=None,
        n_attempts=1,
        total_usage=0,
        failed_attempts=[],
    )
    exc.__cause__ = cause
    return exc


class _SteppingClock:
    """``AsyncClock`` substitute: each ``monotonic`` read advances ``step`` seconds; ``sleep`` only yields to the event loop."""

    def __init__(self, step: float) -> None:
        self._ticks = itertools.count(0.0, step)

    def monotonic(self) -> float:
        return next(self._ticks)

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(0)


@contextmanager
def bounded_poll_clock(step: float = 30.0) -> Generator[None]:
    """The sole polling/retry-wait substitute entry: sleep never actually waits; each clock read advances step seconds.

    ``lib.infra.retry.bind_clock`` binds the stepping clock to the current context; ``retry_async`` backoff and
    ``poll_with_retry`` intervals both use it. Bindings affect only this context and derived tasks, preserving other tests
    and event-loop timing in the same process.

    Broken terminal detection that treats ready tasks as running would otherwise spin effectively forever at almost zero real
    elapsed time, hanging tests instead of failing. The fake clock makes such bugs reach ``max_wait`` within dozens of polls
    and raise ``TimeoutError``, failing quickly and readably.
    """
    from lib.infra.retry import bind_clock

    with bind_clock(_SteppingClock(step)):
        yield


@contextmanager
def captured_provider_job_ids(fail: Exception | None = None) -> Generator[list[dict[str, Any]]]:
    """Handwritten provider_job_id persistence substitute: record arguments without DB writes.

    ``persist_provider_job_id`` is the backend DB boundary; submit/poll backend tests care only what was persisted.
    Yield records keyed by argument names for content assertions, not substitute call-target checks. Nonworker
    ``task_id=None`` paths skip persistence, leaving the list empty. With ``fail``, raise it instead
    to simulate persistence failure.
    """
    records: list[dict[str, Any]] = []

    async def _record(
        task_id: str,
        job_id: str,
        *,
        provider: str,
        endpoint: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if fail is not None:
            raise fail
        records.append(
            {
                "task_id": task_id,
                "job_id": job_id,
                "provider": provider,
                "endpoint": endpoint,
                "base_url": base_url,
            }
        )

    with patch("lib.backends.backend_runtime.persist_provider_job_id", _record):
        yield records


@contextmanager
def captured_ark_clients(module: str, client: Any = None) -> Generator[list[dict[str, Any]]]:
    """create_ark_client recorder: retain constructor arguments and return a supplied or empty client substitute.

    Ark text/image/video backends import the factory in their own modules; callers supply those module paths.
    Assert base_url normalization/auth passthrough on recorded constructor arguments, not substitute call targets.
    """
    from unittest.mock import MagicMock

    created: list[dict[str, Any]] = []
    instance = MagicMock() if client is None else client

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return instance

    with patch(f"{module}.create_ark_client", _create):
        yield created


@contextmanager
def captured_openai_clients(client: Any = None) -> Generator[list[dict[str, Any]]]:
    """AsyncOpenAI constructor recorder: retain arguments and return a supplied or empty client substitute.

    OpenAI-compatible families (openai / agnes text, openai images/video, openai TTS, dashscope/minimax
    video) obtain the SDK entry through ``lib.backends.openai_shared``. Constructor arguments define that boundary
    contract: authentication, base_url normalization, and timeout. Assert recorded arguments, not substitute call targets.
    """
    from unittest.mock import AsyncMock

    created: list[dict[str, Any]] = []
    instance = AsyncMock() if client is None else client

    def _create(**kwargs: Any) -> Any:
        created.append(kwargs)
        return instance

    with patch("lib.backends.openai_shared.AsyncOpenAI", _create):
        yield created


@contextmanager
def patched_instructor_from_openai(patched: Any = None, **patch_kwargs: Any) -> Generator[Any]:
    """Replace ``instructor.from_openai`` at the SDK boundary, yielding the substitute for assertions on ``from_openai`` arguments.

    *patched* is the Instructor client returned by ``from_openai``; omitted values produce the default substitute.
    Forward other keyword arguments (``return_value`` / ``side_effect``) unchanged to ``patch``.
    """
    with patch("instructor.from_openai", **patch_kwargs) as from_openai:
        if patched is not None:
            from_openai.return_value = patched
        yield from_openai


@contextmanager
def captured_backend_construction() -> Generator[list[dict[str, Any]]]:
    """Construction recorders for all four backend registries: replace factories with argument-recording dummy backends, without SDK clients.

    Assembly (``ProviderSpec.build_backend`` / ``lib.backends.text_backends.factory``) outputs the media registry, backend name,
    and constructor arguments; real backends require credentials/network. Replace factories individually by name
    while preserving key sets for ``get_registered_backends`` readers. Record lists support direct argument assertions;
    unregistered names still fail loudly through ``create_backend``.
    """
    from lib.backends.audio_backends import registry as audio_registry
    from lib.backends.image_backends import registry as image_registry
    from lib.backends.text_backends import registry as text_registry
    from lib.backends.video_backends import registry as video_registry

    records: list[dict[str, Any]] = []
    factories: dict[str, dict[str, Any]] = {
        "text": text_registry._BACKEND_FACTORIES,
        "image": image_registry._BACKEND_FACTORIES,
        "video": video_registry._BACKEND_FACTORIES,
        "audio": audio_registry._BACKEND_FACTORIES,
    }

    def _recorder(media: str, name: str) -> Callable[..., Any]:
        def _build(**kwargs: Any) -> Any:
            records.append({"media": media, "backend": name, "kwargs": kwargs})
            return object()

        return _build

    saved = {media: dict(table) for media, table in factories.items()}
    for media, table in factories.items():
        for name in list(table):
            table[name] = _recorder(media, name)
    try:
        yield records
    finally:
        for media, table in factories.items():
            table.clear()
            table.update(saved[media])


class _BlockingFileReadGate:
    """Gate one path's synchronous reads to observe whether async production paths offload reads to threads.

    Reads on the event-loop thread stall in the gate, preventing test coroutines from reaching ``release()``. Release
    timeout records ``event_loop_blocked`` and then reads normally without raising (see ``enter_read``);
    offloaded reads let the loop progress and finish once released. ``assert_read_was_offloaded()`` judges
    event-loop progress, not call records.
    """

    TIMEOUT_SECONDS = 5.0

    def __init__(self, path: Path, method: str) -> None:
        self.path = path
        self.method = method
        self.event_loop_blocked = False
        self._started = threading.Event()
        self._release = threading.Event()

    def enter_read(self) -> None:
        """Called within the gate: record reading started, then await test release.

        Missing release records ``event_loop_blocked`` and then reads normally without raising. Tested paths often have
        retry decorators that would swallow exceptions and succeed on a second pass through an already released gate.
        """
        self._started.set()
        if not self._release.wait(self.TIMEOUT_SECONDS):
            self.event_loop_blocked = True

    async def wait_until_read_started(self) -> None:
        """Wait until gated reading starts; without offloading, the blocked event loop prevents this await returning."""
        started = await asyncio.to_thread(self._started.wait, self.TIMEOUT_SECONDS)
        assert started, f"{self.path.name} 的 {self.method} 未在 {self.TIMEOUT_SECONDS} 秒内开始"

    def release(self) -> None:
        self._release.set()

    def assert_read_was_offloaded(self) -> None:
        assert not self.event_loop_blocked, f"{self.path.name} 的 {self.method} 卡在事件循环线程上：该读未卸载到线程"


@contextmanager
def blocking_file_read_gate(
    monkeypatch: Any,
    path: Path,
    *,
    method: str = "read_bytes",
) -> Generator[_BlockingFileReadGate]:
    """Gate *path* ``Path.<method>`` reads at the filesystem boundary."""
    gate = _BlockingFileReadGate(path, method)
    original = getattr(Path, method)
    target = path.resolve()

    def gated(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.resolve() == target:
            gate.enter_read()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, gated)
    try:
        yield gate
    finally:
        gate.release()


def hook_claim_recheck(monkeypatch, *, before=None, after_first_pass=None) -> None:
    """Insert one artifact change before formal-image task claim rechecks, then allow real rechecks.

    ``before`` runs before every recheck (changes after reference freezing but before provider submission).
    ``after_first_pass`` runs only once after the first successful recheck, testing the second pre-provider checkpoint.
    """

    from server.services.tasks import generation_tasks

    real_recheck = generation_tasks.assert_current_artifact_input_claims_usable
    fired = False

    def _recheck(*args, **kwargs):
        nonlocal fired
        if before is not None:
            before()
        result = real_recheck(*args, **kwargs)
        if after_first_pass is not None and not fired:
            fired = True
            after_first_pass()
        return result

    monkeypatch.setattr(generation_tasks, "assert_current_artifact_input_claims_usable", _recheck)


async def refuse_resume_execution(task: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    """Generation-worker resume-executor substitute: cases must never resume; fail immediately if reached."""
    raise AssertionError(f"unexpected resume execution: {task.get('task_id')}")


def bind_safe_session_factory(monkeypatch, factory) -> None:
    """Bind worker-owned ``safe_session_factory`` to the supplied session factory.

    Worker ``_requeue_single_task`` / ``CapacityTable.from_db`` open their own sessions through module-imported names, bypassing
    injected queues. Dispatch timeout lookup ``lib.config.service.read_video_poll_timeout_seconds`` instead imports the global
    name from ``lib.db`` inside its function; replace both. Callers may supply real
    ``async_sessionmaker`` or a context manager yielding one fake session.
    """
    monkeypatch.setattr("lib.db.safe_session_factory", factory)
    monkeypatch.setattr("lib.generation.generation_worker.safe_session_factory", factory)


class HangingProcess:
    """An asyncio Process-shaped subprocess substitute that never exits on its own; optionally respond to terminate."""

    def __init__(self, *, honors_terminate: bool) -> None:
        self._honors_terminate = honors_terminate
        self._exited = asyncio.Event()
        self.waiting = asyncio.Event()
        self.terminate_requested = asyncio.Event()
        self.returncode: int | None = None
        self.signals: list[str] = []

    def terminate(self) -> None:
        self.signals.append("terminate")
        self.terminate_requested.set()
        if self._honors_terminate:
            self._exit(-15)

    def kill(self) -> None:
        self.signals.append("kill")
        self._exit(-9)

    def _exit(self, code: int) -> None:
        self.returncode = code
        self._exited.set()

    async def wait(self) -> int:
        self.waiting.set()
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    async def communicate(self) -> tuple[bytes, None]:
        await self.wait()
        return b"", None


def use_project_manager(monkeypatch, pm: Any) -> None:
    """Make project entries, routes, and task executors reading ``get_project_manager()`` all receive *pm*.

    Production modules share one process-wide singleton; install the same substitute into every consumer, rather than only
    the directly tested module. Accept either real ``ProjectManager`` or substitutes implementing just used methods.
    """
    for_pm = lambda: pm  # noqa: E731 -- Install the same factory into every consumer below.
    monkeypatch.setattr("lib.asset_library.ingest.get_project_manager", for_pm)
    monkeypatch.setattr("lib.config.resolver.get_project_manager", for_pm)
    monkeypatch.setattr("lib.project.project_migration_guard.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.asset_sheets.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.assets.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.characters.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.derivative_uploads.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.episode_drafts.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.file_view.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.files.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.generate.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.grids.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.object_storage.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.products.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.projects.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.provider_asset_links.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.project_migration.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.project_trash.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.reference_videos.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.shot_uploads.get_project_manager", for_pm)
    monkeypatch.setattr("server.routers.versions.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.currency.upload_finalize.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.grid.grid_split.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.project.end_frame.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.project.provider_asset_import.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.tasks.generation_context.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.tasks.generation_tasks.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.tasks.image_edit_tasks.get_project_manager", for_pm)
    monkeypatch.setattr("server.services.tasks.reference_video_tasks.get_project_manager", for_pm)


def use_generation_context(monkeypatch, resolve: Callable[..., Any]) -> None:
    """Replace generation execution entries' ``resolve_generation_context`` with *resolve*, an async function returning fixed context.

    Generation tasks, formal-image submission, and reference-video tasks each import it at module level; patching one leaves another chain using real resolution.

    For ``MagicMock`` generators without explicit ``choose_reference_image_assets``, supply no applicable provider-material associations:
    send each reference image's content normally. Selection rules themselves are covered in ``tests/integration/lib/asset_library``.
    """

    async def _resolve(*args, **kwargs):
        ctx = await resolve(*args, **kwargs)
        generator = getattr(ctx, "generator", None)
        if isinstance(generator, MagicMock) and not isinstance(generator.choose_reference_image_assets, AsyncMock):
            generator.choose_reference_image_assets = AsyncMock(side_effect=lambda sources: (None,) * len(sources))
        return ctx

    monkeypatch.setattr("server.services.tasks.formal_image_commit.resolve_generation_context", _resolve)
    monkeypatch.setattr("server.services.tasks.generation_tasks.resolve_generation_context", _resolve)
    monkeypatch.setattr("server.services.tasks.reference_video_tasks.resolve_generation_context", _resolve)


def use_video_thumbnail_extractor(monkeypatch, extract: Callable[..., Any]) -> None:
    """Replace video/reference-video task thumbnail extraction with *extract*, avoiding real ffmpeg in cases."""
    monkeypatch.setattr("server.services.tasks.generation_tasks.extract_video_thumbnail", extract)
    monkeypatch.setattr("server.services.tasks.reference_video_tasks.extract_video_thumbnail", extract)


def use_text_generator(monkeypatch, create: Callable[..., Any]) -> None:
    """Replace ``TextGenerator.create`` with *create* so every text-model consumer receives the substitute generator."""
    monkeypatch.setattr("lib.backends.text_generator.TextGenerator.create", create)


def bind_session_factory(monkeypatch, factory) -> None:
    """Bind ``lib.db.async_session_factory`` to the supplied factory, usually conftest ``db_factory``.

    Affects only production code importing ``from lib.db import async_session_factory`` inside functions;
    module-level imports require separate replacement.
    """
    monkeypatch.setattr("lib.db.async_session_factory", factory)


def use_projects_session_factory(monkeypatch, factory) -> None:
    """Project routes open DB sessions through *factory*.

    ``server.routers.projects`` binds ``async_session_factory`` at module level for archive-import cleanup, formal submission, and configuration resolution;
    :func:`bind_session_factory` cannot replace it.
    """
    monkeypatch.setattr("server.routers.projects.async_session_factory", factory)


def use_assets_router_sessions(monkeypatch, factory) -> None:
    """Global asset-library routes open sessions through *factory*, replacing their module-level ``async_session_factory`` binding."""
    monkeypatch.setattr("server.routers.assets.async_session_factory", factory)


def use_object_storage_sdk(monkeypatch, factory: Callable[..., Any]) -> None:
    """Object-storage network boundary: call *factory* instead of ``create_sdk_client`` (accepts ``ObjectStorageConfig``, returns a TOS SDK substitute).

    TOS SDK uses requests, not httpx, so respx cannot intercept; all object access constructs clients here.
    """
    monkeypatch.setattr("lib.object_storage.client.create_sdk_client", factory)


def use_sdk_client(monkeypatch, factory: Callable[..., Any]) -> None:
    """``SessionManager`` calls *factory* for SDK clients (accepts ``options``, usually returns :class:`FakeSDKClient`)."""
    monkeypatch.setattr("server.agent_runtime.session_manager.ClaudeSDKClient", factory)


def use_artifact_registration(monkeypatch, register: Callable[..., Any]) -> None:
    """Formal artifact registration calls *register* (accepting ``project_path, key``), recording registration or injecting failure.

    Persistence imports ``register_current_artifact_if_provable`` inside functions; replacing its ``artifact_activation`` name takes effect.
    """
    monkeypatch.setattr("lib.artifacts.artifact_activation.register_current_artifact_if_provable", register)


def use_upload_claim_registration(monkeypatch, register: Callable[..., Any]) -> None:
    """User-uploaded output registration (``register_current_resource_artifact``) calls *register* to inject failure.

    Formal upload/import writes register through ``server.services.currency.upload_finalize``; commit-hook failure rolls back the whole write.
    """
    monkeypatch.setattr("server.services.currency.upload_finalize.register_current_resource_artifact", register)


def use_provider_env(monkeypatch, load: Callable[..., Any]) -> None:
    """Agent session option assembly calls async *load* for injected provider environment variables, without DB reads."""
    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", load)


def use_reference_projection(monkeypatch, project: Callable[..., Any]) -> None:
    """Reference-video routes and batch video admission use *project* for unit projection, matching ``project_reference_unit_request`` signatures."""
    monkeypatch.setattr("server.routers.reference_videos.project_reference_unit_request", project)
    monkeypatch.setattr("server.services.admission.video_batch_admission.project_reference_unit_request", project)


def use_active_tasks_lookup(monkeypatch, lookup: Callable[..., Any]) -> None:
    """Batch video admission calls async *lookup* for in-flight tasks, bypassing the generation queue."""
    monkeypatch.setattr("server.services.admission.video_batch_admission.get_active_tasks_for_resources", lookup)


def use_project_json_write(monkeypatch, write: Callable[..., Any]) -> None:
    """Replace ``ProjectManager`` JSON filesystem-write boundaries with *write*."""
    monkeypatch.setattr("lib.project.project_manager.atomic_write_json", write)


def fail_project_json_write(monkeypatch, project_file: Path) -> None:
    """``ProjectManager`` raises ``OSError("injected project write failure")`` for *project_file*, writing other files normally.

    Filesystem failure injection verifies consistency between manifests and disk files when project.json writes fail.
    """
    import lib.project.project_manager as project_manager_module

    real_write = project_manager_module.atomic_write_json

    def _fail_project_write(path, data):
        if path == project_file:
            raise OSError("injected project write failure")
        return real_write(path, data)

    use_project_json_write(monkeypatch, _fail_project_write)


def use_generation_queue(monkeypatch, queue: Any) -> None:
    """Generation/reference-video routes' ``get_generation_queue()`` returns the enqueue substitute *queue*."""
    monkeypatch.setattr("server.routers.generate.get_generation_queue", lambda: queue)
    monkeypatch.setattr("server.routers.reference_videos.get_generation_queue", lambda: queue)


def use_backend_assembler(monkeypatch, assemble: Callable[..., Any]) -> None:
    """Generation-context assembly calls *assemble*; all other resolution (configuration, projects, DB) stays real, replacing only provider backends."""
    monkeypatch.setattr("server.services.tasks.generation_context.assemble_backend", assemble)


async def stage_and_commit(commit_formal_output, staged: Path, current: Path, content: bytes, metadata: dict) -> int:
    """Substitute generators write outputs and commit formal versions.

    Match MediaGenerator: write staging artifacts in worker threads and invoke formal commit callbacks, with backend writes
    threaded and commit callbacks through ``run_noninterruptible_sync``.
    """

    def _run() -> int:
        staged.write_bytes(content)
        return commit_formal_output(staged, current, metadata)

    return await asyncio.to_thread(_run)
