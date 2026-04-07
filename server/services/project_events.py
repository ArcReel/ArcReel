"""
Project data change detection and SSE fanout for workspace realtime updates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib import PROJECT_ROOT
from lib.project_change_hints import (
    ProjectChangeBatch,
    ProjectChangeSource,
    project_change_source,
    register_project_change_batch_listener,
    register_project_change_listener,
)
from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)

PROJECT_EVENTS_POLL_SECONDS = 0.5


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha1(_stable_json(value).encode("utf-8")).hexdigest()


@dataclass
class _ProjectChannel:
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    scan_now: asyncio.Event = field(default_factory=asyncio.Event)
    pending_sources: set[ProjectChangeSource] = field(default_factory=set)
    task: asyncio.Task | None = None
    snapshot: dict[str, Any] | None = None
    fingerprint: str = ""


class ProjectEventService:
    def __init__(
        self,
        project_root: Path | None = None,
        *,
        poll_interval: float = PROJECT_EVENTS_POLL_SECONDS,
    ):
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.pm = ProjectManager(self.project_root / "projects")
        self.poll_interval = max(0.1, float(poll_interval))
        self._channels: dict[str, _ProjectChannel] = {}
        self._listener_unregister = None
        self._batch_listener_unregister = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._listener_unregister is not None or self._batch_listener_unregister is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._listener_unregister = register_project_change_listener(self._on_hint)
        self._batch_listener_unregister = register_project_change_batch_listener(self._on_batch_hint)

    async def shutdown(self) -> None:
        unregister = self._listener_unregister
        self._listener_unregister = None
        if unregister is not None:
            unregister()
        batch_unregister = self._batch_listener_unregister
        self._batch_listener_unregister = None
        if batch_unregister is not None:
            batch_unregister()

        tasks = [channel.task for channel in self._channels.values() if channel.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._channels.clear()
        self._loop = None

    async def subscribe(self, project_name: str) -> tuple[asyncio.Queue, dict[str, Any]]:
        await asyncio.to_thread(self.pm.get_project_path, project_name)
        channel = self._channels.get(project_name)
        if channel is None:
            channel = _ProjectChannel()
            self._channels[project_name] = channel

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        channel.subscribers.add(queue)

        if channel.task is None or channel.task.done():
            channel.ready_event = asyncio.Event()
            channel.scan_now = asyncio.Event()
            channel.pending_sources.clear()
            channel.task = asyncio.create_task(
                self._watch_project(project_name, channel),
                name=f"project-events-{project_name}",
            )

        await channel.ready_event.wait()
        return queue, self._build_snapshot_payload(project_name, channel)

    async def unsubscribe(self, project_name: str, queue: asyncio.Queue) -> None:
        channel = self._channels.get(project_name)
        if channel is None:
            return
        channel.subscribers.discard(queue)
        if channel.subscribers:
            return
        task = channel.task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._channels.pop(project_name, None)

    def _on_hint(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changed_paths: tuple[str, ...],
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._apply_hint,
            project_name,
            source,
            changed_paths,
        )

    def _on_batch_hint(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changes: tuple[ProjectChangeBatch, ...],
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._apply_emitted_batch,
            project_name,
            source,
            changes,
        )

    def _apply_hint(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changed_paths: tuple[str, ...],
    ) -> None:
        channel = self._channels.get(project_name)
        if channel is None:
            return
        channel.pending_sources.add(source)
        channel.scan_now.set()
        logger.debug(
            "项目变更 hint project=%s source=%s paths=%s",
            project_name,
            source,
            changed_paths,
        )

    def _apply_emitted_batch(
        self,
        project_name: str,
        source: ProjectChangeSource,
        changes: tuple[ProjectChangeBatch, ...],
    ) -> None:
        channel = self._channels.get(project_name)
        if channel is None or not changes:
            return

        channel.scan_now.clear()

        # 将同步文件 I/O 放到线程池执行，避免阻塞事件循环
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        async def _do():
            try:
                snapshot, fingerprint = await asyncio.to_thread(self._rebuild_snapshot, project_name)
                channel.snapshot = snapshot
                channel.fingerprint = fingerprint
                channel.pending_sources.clear()
            except Exception:
                logger.exception("构建显式项目事件快照失败 project=%s", project_name)
                return

            payload = {
                "project_name": project_name,
                "batch_id": uuid.uuid4().hex,
                "fingerprint": fingerprint,
                "generated_at": _utc_now_iso(),
                "source": source,
                "changes": [dict(change) for change in changes],
            }
            self._broadcast(project_name, channel, "changes", payload)

        asyncio.ensure_future(_do())

    def _rebuild_snapshot(self, project_name: str) -> tuple[dict[str, Any], str]:
        """同步方法：重建快照并返回 (snapshot, fingerprint)。"""
        self._ensure_script_index_synced(project_name)
        snapshot = self._build_snapshot(project_name)
        return snapshot, _fingerprint(snapshot)

    async def _watch_project(self, project_name: str, channel: _ProjectChannel) -> None:
        try:
            while channel.subscribers:
                try:
                    await asyncio.to_thread(self._scan_project, project_name, channel)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("项目事件扫描失败 project=%s", project_name)
                finally:
                    channel.ready_event.set()

                try:
                    await asyncio.wait_for(channel.scan_now.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    continue
                finally:
                    channel.scan_now.clear()
        except asyncio.CancelledError:
            raise

    def _scan_project(self, project_name: str, channel: _ProjectChannel) -> None:
        self._ensure_script_index_synced(project_name)
        snapshot = self._build_snapshot(project_name)
        fingerprint = _fingerprint(snapshot)

        if channel.snapshot is None:
            channel.snapshot = snapshot
            channel.fingerprint = fingerprint
            channel.pending_sources.clear()
            return

        if fingerprint == channel.fingerprint:
            channel.pending_sources.clear()
            return

        source = self._resolve_batch_source(channel.pending_sources)
        channel.pending_sources.clear()
        changes = self._diff_snapshots(channel.snapshot, snapshot)
        channel.snapshot = snapshot
        channel.fingerprint = fingerprint
        if not changes:
            return

        payload = {
            "project_name": project_name,
            "batch_id": uuid.uuid4().hex,
            "fingerprint": fingerprint,
            "generated_at": _utc_now_iso(),
            "source": source,
            "changes": changes,
        }
        self._broadcast(project_name, channel, "changes", payload)

    def _build_snapshot_payload(
        self,
        project_name: str,
        channel: _ProjectChannel,
    ) -> dict[str, Any]:
        return {
            "project_name": project_name,
            "fingerprint": channel.fingerprint,
            "generated_at": _utc_now_iso(),
        }

    @staticmethod
    def _resolve_batch_source(
        pending_sources: set[ProjectChangeSource],
    ) -> ProjectChangeSource:
        if "worker" in pending_sources:
            return "worker"
        if "webui" in pending_sources:
            return "webui"
        return "filesystem"

    def _broadcast(
        self,
        project_name: str,
        channel: _ProjectChannel,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        stale: list[asyncio.Queue] = []
        for subscriber in channel.subscribers:
            try:
                subscriber.put_nowait((event, payload))
            except asyncio.QueueFull:
                stale.append(subscriber)
        for subscriber in stale:
            channel.subscribers.discard(subscriber)
        if stale:
            logger.warning(
                "项目事件订阅队列溢出，移除 %s 个订阅者 project=%s",
                len(stale),
                project_name,
            )

    def _ensure_script_index_synced(self, project_name: str) -> None:
        project_path = self.pm.get_project_path(project_name)
        scripts_dir = project_path / "scripts"
        if not scripts_dir.exists():
            return

        project = self.pm.load_project(project_name)
        current_episodes = {
            int(ep.get("episode")): {
                "title": str(ep.get("title") or ""),
                "script_file": str(ep.get("script_file") or ""),
            }
            for ep in project.get("episodes", [])
            if isinstance(ep, dict) and isinstance(ep.get("episode"), int)
        }

        for script_path in sorted(scripts_dir.glob("*.json")):
            try:
                script = self.pm.load_script(project_name, script_path.name)
            except Exception:
                logger.warning("跳过无法读取的剧本文件 project=%s file=%s", project_name, script_path.name)
                continue

            episode = script.get("episode")
            if not isinstance(episode, int):
                continue
            title = str(script.get("title") or "")
            expected_script_file = f"scripts/{script_path.name}"
            existing = current_episodes.get(episode)
            if existing and existing["title"] == title and existing["script_file"] == expected_script_file:
                continue

            with project_change_source("filesystem"):
                self.pm.sync_episode_from_script(project_name, script_path.name)
            current_episodes[episode] = {
                "title": title,
                "script_file": expected_script_file,
            }

    def _build_snapshot(self, project_name: str) -> dict[str, Any]:
        project = self.pm.load_project(project_name)
        scripts_dir = self.pm.get_project_path(project_name) / "scripts"
        project_meta = {
            "title": str(project.get("title") or ""),
            "style": str(project.get("style") or ""),
            "style_image": str(project.get("style_image") or ""),
            "style_description": str(project.get("style_description") or ""),
        }

        characters = {
            name: {
                "description": str(data.get("description") or ""),
                "voice_style": str(data.get("voice_style") or ""),
                "character_sheet": str(data.get("character_sheet") or ""),
                "reference_image": str(data.get("reference_image") or ""),
            }
            for name, data in sorted(project.get("characters", {}).items())
            if isinstance(data, dict)
        }

        clues = {
            name: {
                "type": str(data.get("type") or ""),
                "description": str(data.get("description") or ""),
                "importance": str(data.get("importance") or ""),
                "clue_sheet": str(data.get("clue_sheet") or ""),
            }
            for name, data in sorted(project.get("clues", {}).items())
            if isinstance(data, dict)
        }

        overview = project.get("overview")
        if isinstance(overview, dict):
            normalized_overview = {
                key: overview.get(key)
                for key in ("synopsis", "genre", "theme", "world_setting", "generated_at")
                if key in overview
            }
        else:
            normalized_overview = {}

        episodes = {
            str(ep["episode"]): {
                "episode": int(ep["episode"]),
                "title": str(ep.get("title") or ""),
                "script_file": str(ep.get("script_file") or ""),
            }
            for ep in sorted(
                [
                    ep
                    for ep in project.get("episodes", [])
                    if isinstance(ep, dict) and isinstance(ep.get("episode"), int)
                ],
                key=lambda value: value["episode"],
            )
        }

        scripts: dict[str, Any] = {}
        if scripts_dir.exists():
            for script_path in sorted(scripts_dir.glob("*.json")):
                try:
                    script = self.pm.load_script(project_name, script_path.name)
                except Exception:
                    logger.warning("跳过无法解析的剧本快照 project=%s file=%s", project_name, script_path.name)
                    continue
                scripts[script_path.name] = self._normalize_script_snapshot(script)

        return {
            "project": {
                "meta": project_meta,
                "characters": characters,
                "clues": clues,
                "overview": normalized_overview,
                "episodes": episodes,
            },
            "scripts": scripts,
        }

    def _normalize_script_snapshot(self, script: dict[str, Any]) -> dict[str, Any]:
        content_mode = str(script.get("content_mode") or "narration")
        raw_items = script.get("segments" if content_mode == "narration" else "scenes", [])
        if not isinstance(raw_items, list):
            raw_items = []
        id_field = "segment_id" if content_mode == "narration" else "scene_id"
        chars_field = "characters_in_segment" if content_mode == "narration" else "characters_in_scene"
        clues_field = "clues_in_segment" if content_mode == "narration" else "clues_in_scene"

        items: dict[str, Any] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get(id_field) or "")
            if not item_id:
                continue
            assets = item.get("generated_assets")
            if not isinstance(assets, dict):
                assets = {}
            items[item_id] = {
                "duration_seconds": item.get("duration_seconds"),
                "segment_break": bool(item.get("segment_break")),
                "characters": sorted(str(name) for name in item.get(chars_field, []) or []),
                "clues": sorted(str(name) for name in item.get(clues_field, []) or []),
                "image_prompt": item.get("image_prompt"),
                "video_prompt": item.get("video_prompt"),
                "generated_assets": {
                    "storyboard_image": str(assets.get("storyboard_image") or ""),
                    "video_clip": str(assets.get("video_clip") or ""),
                    "video_uri": str(assets.get("video_uri") or ""),
                    "status": str(assets.get("status") or ""),
                },
            }

        return {
            "episode": script.get("episode"),
            "title": str(script.get("title") or ""),
            "content_mode": content_mode,
            "items": items,
        }

    def _diff_snapshots(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        changes.extend(
            self._diff_named_entities(
                entity_type="character",
                previous_items=previous["project"]["characters"],
                current_items=current["project"]["characters"],
                pane="characters",
            )
        )
        changes.extend(
            self._diff_named_entities(
                entity_type="clue",
                previous_items=previous["project"]["clues"],
                current_items=current["project"]["clues"],
                pane="clues",
            )
        )
        if previous["project"]["meta"] != current["project"]["meta"]:
            changes.append(
                {
                    "entity_type": "project",
                    "action": "updated",
                    "entity_id": "project",
                    "label": "项目设置",
                    "focus": None,
                    "important": False,
                }
            )
        if previous["project"]["overview"] != current["project"]["overview"]:
            changes.append(
                {
                    "entity_type": "overview",
                    "action": "updated",
                    "entity_id": "overview",
                    "label": "项目概览",
                    "focus": None,
                    "important": False,
                }
            )
        changes.extend(
            self._diff_episodes(
                previous["project"]["episodes"],
                current["project"]["episodes"],
            )
        )
        changes.extend(
            self._diff_script_items(
                previous["scripts"],
                current["scripts"],
            )
        )
        return changes

    def _diff_named_entities(
        self,
        *,
        entity_type: str,
        previous_items: dict[str, Any],
        current_items: dict[str, Any],
        pane: str,
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        previous_keys = set(previous_items)
        current_keys = set(current_items)
        for name in sorted(current_keys - previous_keys):
            changes.append(
                self._build_entity_change(
                    entity_type=entity_type,
                    action="created",
                    entity_id=name,
                    label=f"{'角色' if entity_type == 'character' else '线索'}「{name}」",
                    focus={
                        "pane": pane,
                        "anchor_type": entity_type,
                        "anchor_id": name,
                    },
                    important=True,
                )
            )
        for name in sorted(previous_keys - current_keys):
            changes.append(
                self._build_entity_change(
                    entity_type=entity_type,
                    action="deleted",
                    entity_id=name,
                    label=f"{'角色' if entity_type == 'character' else '线索'}「{name}」",
                    focus=None,
                    important=False,
                )
            )
        for name in sorted(previous_keys & current_keys):
            if previous_items[name] == current_items[name]:
                continue
            changes.append(
                self._build_entity_change(
                    entity_type=entity_type,
                    action="updated",
                    entity_id=name,
                    label=f"{'角色' if entity_type == 'character' else '线索'}「{name}」",
                    focus={
                        "pane": pane,
                        "anchor_type": entity_type,
                        "anchor_id": name,
                    },
                    important=True,
                )
            )
        return changes

    def _diff_episodes(
        self,
        previous_items: dict[str, Any],
        current_items: dict[str, Any],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        previous_keys = set(previous_items)
        current_keys = set(current_items)
        for episode_key in sorted(current_keys - previous_keys, key=int):
            episode = current_items[episode_key]
            changes.append(
                self._build_entity_change(
                    entity_type="episode",
                    action="created",
                    entity_id=episode_key,
                    label=f"第 {episode['episode']} 集",
                    script_file=episode.get("script_file"),
                    episode=episode["episode"],
                    focus=None,
                    important=True,
                )
            )
        for episode_key in sorted(previous_keys & current_keys, key=int):
            if previous_items[episode_key] == current_items[episode_key]:
                continue
            episode = current_items[episode_key]
            changes.append(
                self._build_entity_change(
                    entity_type="episode",
                    action="updated",
                    entity_id=episode_key,
                    label=f"第 {episode['episode']} 集",
                    script_file=episode.get("script_file"),
                    episode=episode["episode"],
                    focus=None,
                    important=True,
                )
            )
        return changes

    def _diff_script_items(
        self,
        previous_scripts: dict[str, Any],
        current_scripts: dict[str, Any],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for script_file in sorted(set(previous_scripts) & set(current_scripts)):
            previous_meta = previous_scripts[script_file]
            current_meta = current_scripts[script_file]
            previous_items = previous_meta.get("items", {})
            current_items = current_meta.get("items", {})
            for item_id in sorted(set(current_items) - set(previous_items)):
                changes.append(
                    self._build_script_item_change(
                        action="created",
                        item_id=item_id,
                        script_file=script_file,
                        script_meta=current_meta,
                        important=True,
                    )
                )
            for item_id in sorted(set(previous_items) - set(current_items)):
                changes.append(
                    self._build_script_item_change(
                        action="deleted",
                        item_id=item_id,
                        script_file=script_file,
                        script_meta=previous_meta,
                        important=False,
                    )
                )
            for item_id in sorted(set(previous_items) & set(current_items)):
                previous_item = previous_items[item_id]
                current_item = current_items[item_id]
                focus = self._build_script_item_focus(item_id, current_meta)
                label = self._build_script_item_label(item_id, current_meta)
                if self._became_truthy(
                    previous_item["generated_assets"].get("storyboard_image"),
                    current_item["generated_assets"].get("storyboard_image"),
                ):
                    changes.append(
                        self._build_entity_change(
                            entity_type="segment",
                            action="storyboard_ready",
                            entity_id=item_id,
                            label=label,
                            script_file=script_file,
                            episode=current_meta.get("episode"),
                            focus=focus,
                            important=True,
                        )
                    )
                if self._became_truthy(
                    previous_item["generated_assets"].get("video_clip"),
                    current_item["generated_assets"].get("video_clip"),
                ):
                    changes.append(
                        self._build_entity_change(
                            entity_type="segment",
                            action="video_ready",
                            entity_id=item_id,
                            label=label,
                            script_file=script_file,
                            episode=current_meta.get("episode"),
                            focus=focus,
                            important=True,
                        )
                    )

                previous_body = {key: value for key, value in previous_item.items() if key != "generated_assets"}
                current_body = {key: value for key, value in current_item.items() if key != "generated_assets"}
                if previous_body != current_body:
                    changes.append(
                        self._build_entity_change(
                            entity_type="segment",
                            action="updated",
                            entity_id=item_id,
                            label=label,
                            script_file=script_file,
                            episode=current_meta.get("episode"),
                            focus=focus,
                            important=True,
                        )
                    )
        return changes

    @staticmethod
    def _build_script_item_focus(
        item_id: str,
        script_meta: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "pane": "episode",
            "episode": script_meta.get("episode"),
            "anchor_type": "segment",
            "anchor_id": item_id,
        }

    @staticmethod
    def _build_script_item_label(item_id: str, script_meta: dict[str, Any]) -> str:
        content_mode = str(script_meta.get("content_mode") or "narration")
        noun = "分镜" if content_mode == "narration" else "场景"
        return f"{noun}「{item_id}」"

    def _build_script_item_change(
        self,
        *,
        action: str,
        item_id: str,
        script_file: str,
        script_meta: dict[str, Any],
        important: bool,
    ) -> dict[str, Any]:
        focus = self._build_script_item_focus(item_id, script_meta) if action != "deleted" else None
        return self._build_entity_change(
            entity_type="segment",
            action=action,
            entity_id=item_id,
            label=self._build_script_item_label(item_id, script_meta),
            script_file=script_file,
            episode=script_meta.get("episode"),
            focus=focus,
            important=important,
        )

    @staticmethod
    def _became_truthy(previous: Any, current: Any) -> bool:
        return bool(current) and not bool(previous)

    @staticmethod
    def _build_entity_change(
        *,
        entity_type: str,
        action: str,
        entity_id: str,
        label: str,
        focus: dict[str, Any] | None,
        important: bool,
        script_file: str | None = None,
        episode: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "entity_type": entity_type,
            "action": action,
            "entity_id": entity_id,
            "label": label,
            "focus": focus,
            "important": important,
        }
        if script_file:
            payload["script_file"] = script_file
        if isinstance(episode, int):
            payload["episode"] = episode
        return payload
