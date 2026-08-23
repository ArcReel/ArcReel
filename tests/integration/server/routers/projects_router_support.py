"""server.routers.projects 测试共享的替身与 helper。"""

import json
import re
import shutil
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from lib.project_manager import EmptySourceError
from lib.script_batch_edit import (
    InsertAfterOperation,
    MoveAfterOperation,
    RemoveOperation,
    ScriptBatchEditLocation,
    ScriptBatchEditProblem,
    ScriptBatchEditResult,
    script_revision,
)
from lib.script_editor import ScriptEditError, patch_field, resolve_items
from lib.speech_composition import admit_script_unit
from lib.workflow_state import ArtifactCount, EpisodesSummary, EpisodeSummary, ProjectSummary


class _OverviewProbe(BaseModel):
    """仅用于让 fake 生成器抛出真实的 pydantic ValidationError（也是 ValueError 子类）。"""

    synopsis: str


from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import projects
from tests.auth_deps import AUTH_DEPENDENCIES


class _FakePM:
    def __init__(self, base: Path):
        self.base = base
        self.projects_root = base
        self.project_data = {
            "ready": {
                "title": "Ready",
                "style": "Anime",
                "generation_mode": "storyboard",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
                "overview": {"synopsis": "old"},
            },
            "broken": {
                "title": "Broken",
                "style": "",
                "episodes": [],
            },
            "ad-ready": {
                "title": "Ad Ready",
                "style": "Realistic",
                "content_mode": "ad",
                "target_duration": 60,
                "brief": "",
                "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
            },
        }
        self.scripts = {
            ("ready", "episode_1.json"): {
                "content_mode": "drama",
                "scenes": [{"scene_id": "001", "duration_seconds": 8}],
            },
            ("ready", "narration.json"): {
                "content_mode": "narration",
                "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
            },
        }
        self.created = set()
        self.generated_names = ["project-aa11bb22", "project-cc33dd44"]
        self.profile_reset_calls: list[str] = []
        (self.base / "ready" / "storyboards").mkdir(parents=True, exist_ok=True)
        (self.base / "ad-ready" / "scripts").mkdir(parents=True, exist_ok=True)
        (self.base / "ready" / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        (self.base / "empty").mkdir(parents=True, exist_ok=True)
        (self.base / "remove-me").mkdir(parents=True, exist_ok=True)
        # generate_overview 的名义分支目标：存在但内容有问题的项目（源目录空/供应商未配置/json 损坏）
        (self.base / "bad").mkdir(parents=True, exist_ok=True)
        (self.base / "no-provider").mkdir(parents=True, exist_ok=True)
        (self.base / "corrupted").mkdir(parents=True, exist_ok=True)
        (self.base / "bad-schema").mkdir(parents=True, exist_ok=True)
        # 上传后概览生成失败的软降级路径：项目存在，但 generate_overview 抛带路径异常
        (self.base / "leaky").mkdir(parents=True, exist_ok=True)

    def list_projects(self):
        return ["ready", "empty", "broken"]

    def project_exists(self, name):
        return name in {"ready", "broken", "leaky"}

    def load_project(self, name):
        if name == "broken":
            raise RuntimeError("broken")
        if name not in self.project_data:
            raise FileNotFoundError(name)
        return self.project_data[name]

    def get_project_path(self, name):
        if name == "illegal-name":
            raise ValueError(f"非法项目名称: '{name}'")
        path = self.base / name
        if not path.exists():
            raise FileNotFoundError(name)
        return path

    @contextmanager
    def locked_source_mutation(self, name):
        source_dir = self.get_project_path(name) / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        yield source_dir

    def delete_project_directory(self, name):
        shutil.rmtree(self.get_project_path(name))

    def get_project_status(self, name):
        return {"current_stage": "source_ready"}

    def get_agent_profile_status(self, project_dir):
        assert project_dir == self.base / "ready"
        return {
            "customized": True,
            "customized_files": ["CLAUDE.md", ".claude/agents/legacy.md"],
        }

    def force_resync_profile(self, project_dir):
        self.profile_reset_calls.append(project_dir.name)
        return {"repaired": 2, "errors": 0}

    def create_project(self, name, content_mode="narration"):
        if not name or not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise ValueError("项目标识仅允许英文字母、数字和中划线")
        if name == "exists":
            raise FileExistsError(name)
        self.created.add(name)
        (self.base / name).mkdir(parents=True, exist_ok=True)

    def generate_project_name(self, title):
        return self.generated_names.pop(0)

    def create_project_metadata(
        self,
        name,
        title,
        style,
        content_mode,
        aspect_ratio="9:16",
        default_duration=None,
        style_template_id=None,
        extras=None,
        target_duration=None,
        brief=None,
        source_kind=None,
    ):
        payload = {
            "title": (title or name),
            "style": style or "",
            "content_mode": content_mode,
            "source_kind": source_kind or "novel",
            "aspect_ratio": aspect_ratio,
            "episodes": [],
        }
        if content_mode == "ad":
            # 镜像真实 ProjectManager 的 ad 形状：常量直接取自生产代码，避免第二份真相
            from lib.project_manager import ProjectManager

            payload["target_duration"] = (
                target_duration if target_duration is not None else ProjectManager.AD_DEFAULT_TARGET_DURATION
            )
            payload["brief"] = brief if brief is not None else ""
            payload["episodes"] = [dict(ProjectManager.AD_SINGLE_EPISODE)]
        if default_duration is not None:
            payload["default_duration"] = default_duration
        if style_template_id is not None:
            payload["style_template_id"] = style_template_id
        if extras:
            payload.update(extras)
        self.project_data[name] = payload
        return payload

    def save_project(self, name, payload):
        self.project_data[name] = payload

    def load_script(self, name, script_file):
        if script_file.startswith("scripts/"):
            script_file = script_file[len("scripts/") :]
        key = (name, script_file)
        if key not in self.scripts:
            raise FileNotFoundError(script_file)
        return self.scripts[key]

    @staticmethod
    def normalize_script_filename(script_file):
        return script_file.removeprefix("scripts/")

    def save_script(self, name, payload, script_file):
        if script_file.startswith("scripts/"):
            script_file = script_file[len("scripts/") :]
        self.scripts[(name, script_file)] = payload

    def update_project(self, name, mutate_fn):
        # 复刻真实 ProjectManager.update_project：load → mutate → save 单一事务，
        # 并返回迁移后的 project dict（调用方据此回前端，无需二次 load_project）。
        # deepcopy 后再 mutate，使异常时（save 未执行）backing store 不被原地突变污染，
        # 忠实于真实 PM「读裸 JSON、出错不写回」的语义。
        project = deepcopy(self.load_project(name))
        mutate_fn(project)
        self.save_project(name, project)
        return project

    def update_project_reconciling_episode_bindings(self, name, mutate_fn):
        return self.update_project(name, mutate_fn)

    @contextmanager
    def locked_script(self, name, script_file):
        # 复刻真实 ProjectManager.locked_script：load → yield → save，异常时跳过写回。
        # deepcopy 同上，确保 with 体内抛异常时原始存储对象保持不变。
        script = deepcopy(self.load_script(name, script_file))
        yield script
        self.save_script(name, script, script_file)

    @contextmanager
    def locked_episode_script(self, name, resolve_script_file, *, validate=True, on_commit=None):
        # 复刻真实 ProjectManager.locked_episode_script：解析 episode→script_file →
        # 锁内读改写脚本 → 内联把 title/script_file 镜像回 project.json episodes[]
        # （仅当脚本含 episode int，与真实 _apply_episode_sync 触发条件一致）。
        script_file = resolve_script_file(self.load_project(name))
        norm = script_file[len("scripts/") :] if script_file.startswith("scripts/") else script_file
        script = deepcopy(self.load_script(name, norm))
        yield script
        self.save_script(name, script, norm)
        if isinstance(script.get("episode"), int):
            project = deepcopy(self.load_project(name))
            episodes = project.setdefault("episodes", [])
            entry = next((e for e in episodes if e.get("episode") == script["episode"]), None)
            if entry is None:
                entry = {"episode": script["episode"]}
                episodes.append(entry)
            entry["title"] = script.get("title", "")
            entry["script_file"] = f"scripts/{norm}"
            self.save_project(name, project)
        if on_commit is not None:
            on_commit(self.base / name / "scripts" / norm)

    async def generate_overview(self, name):
        if name == "ready":
            return {"synopsis": "generated"}
        if name == "leaky":
            # 模拟底层异常文本携带服务器绝对路径（如文件读写失败），
            # 上传端点的软降级分支不得把裸 str(e) 透传给客户端。
            raise RuntimeError("open failed: /Users/secret/projects/leaky/source/novel.txt")
        if name == "no-provider":
            raise ValueError("未找到可用的 text 供应商")
        if name == "corrupted":
            # 模拟供应商解析链路内部重新 load_project 时命中损坏的 project.json：
            # JSONDecodeError 是 ValueError 子类，不该被误判为「未配置供应商」
            json.loads("{not valid json")
        if name == "bad-schema":
            # 模拟模型输出未通过 schema 校验：pydantic ValidationError 同样是 ValueError 子类，
            # 不该被误判为「未配置供应商」
            _OverviewProbe.model_validate({})
        raise EmptySourceError("source missing")


class _RejectedFakeEdit(Exception):
    def __init__(self, result: ScriptBatchEditResult):
        self.result = result


class _FakeBatchEditor:
    """Router-unit-test adapter; aggregate validation is covered against the real service separately."""

    def __init__(self, pm: _FakePM):
        self.pm = pm

    def execute(self, project_name, command):
        script_file = command.script
        assert script_file is not None
        before_revision = script_revision(self.pm.load_script(project_name, script_file))
        if command.expected_revision != before_revision:
            return self._failure(script_file, before_revision, "revision_conflict", None, None)
        affected: list[str] = []
        try:
            with self.pm.locked_script(project_name, script_file) as script:
                for index, operation in enumerate(command.operations):
                    items, id_field, kind = resolve_items(script)
                    item_id = getattr(operation, "id", None)
                    if isinstance(operation, InsertAfterOperation):
                        raise AssertionError("insert is not used by project router unit tests")
                    try:
                        item_index = next(i for i, item in enumerate(items) if item.get(id_field) == item_id)
                    except StopIteration:
                        raise _RejectedFakeEdit(
                            self._failure(script_file, before_revision, "operation_invalid", index, item_id)
                        ) from None
                    before = admit_script_unit(kind, items[item_index])
                    if isinstance(operation, MoveAfterOperation):
                        item = items.pop(item_index)
                        if operation.after_id is None:
                            insert_at = 0
                        else:
                            try:
                                insert_at = (
                                    next(
                                        i
                                        for i, existing in enumerate(items)
                                        if existing.get(id_field) == operation.after_id
                                    )
                                    + 1
                                )
                            except StopIteration:
                                raise _RejectedFakeEdit(
                                    self._failure(script_file, before_revision, "operation_invalid", index, item_id)
                                ) from None
                        items.insert(insert_at, item)
                    elif isinstance(operation, RemoveOperation):
                        items.pop(item_index)
                    else:
                        before_content = admit_script_unit(kind, items[item_index], ignore_marker=True)
                        try:
                            for field, value in operation.fields.items():
                                patch_field(script, operation.id, field, value)
                        except ScriptEditError:
                            raise _RejectedFakeEdit(
                                self._failure(script_file, before_revision, "operation_invalid", index, item_id)
                            ) from None
                        after_content = admit_script_unit(kind, items[item_index], ignore_marker=True)
                        if before_content.preparation != after_content.preparation and after_content.allowed:
                            items[item_index].pop("needs_replan", None)
                    if item_id not in affected:
                        affected.append(item_id)
                    if not isinstance(operation, RemoveOperation):
                        after = admit_script_unit(
                            kind, items[next(i for i, v in enumerate(items) if v.get(id_field) == item_id)]
                        )
                        if after.preparation != before.preparation and not after.allowed:
                            problems = tuple(
                                ScriptBatchEditProblem(
                                    code=problem.code.value,
                                    operation_index=index,
                                    unit_id=problem.unit_id,
                                    locations=tuple(
                                        ScriptBatchEditLocation(path=location.path, line=location.line)
                                        for location in problem.locations
                                    ),
                                    reason=problem.reason.value,
                                    next_action=problem.action.value,
                                )
                                for problem in after.problems
                                if problem.code.value != "needs_replan"
                                or not any(p.code.value != "needs_replan" for p in after.problems)
                            )
                            raise _RejectedFakeEdit(
                                ScriptBatchEditResult(
                                    success=False,
                                    script=script_file,
                                    episode=None,
                                    before_revision=before_revision,
                                    revision=before_revision,
                                    problems=problems,
                                )
                            )
        except _RejectedFakeEdit as exc:
            return exc.result
        revision = script_revision(self.pm.load_script(project_name, script_file))
        return ScriptBatchEditResult(
            success=True,
            script=script_file,
            episode=None,
            before_revision=before_revision,
            revision=revision,
            affected_ids=tuple(affected),
        )

    @staticmethod
    def _failure(script_file, revision, code, index, item_id):
        return ScriptBatchEditResult(
            success=False,
            script=script_file,
            episode=None,
            before_revision=revision,
            revision=revision,
            problems=(
                ScriptBatchEditProblem(
                    code=code,
                    operation_index=index,
                    unit_id=item_id,
                    reason="operation_invalid",
                    next_action="fix_operation",
                ),
            ),
        )


class _FakeSummaries:
    """项目摘要投影替身：记录列表端点是否把一次性加载的剧本 map 交给它。"""

    def __init__(self):
        self.last_preloaded_scripts: dict | None = None

    def get_project_summary(self, name, *, preloaded_scripts=None) -> ProjectSummary:
        self.last_preloaded_scripts = preloaded_scripts
        return ProjectSummary(
            phase="production",
            phase_progress=0.5,
            needs_repair=False,
            repair_reason=None,
            assets={"character": ArtifactCount(total=1, available=0, stale=0)},
            episodes_summary=EpisodesSummary(total=1, scripted=1, in_production=1, completed=0),
            episodes=[
                EpisodeSummary(
                    episode=1,
                    script_status="generated",
                    status="in_production",
                    item_count=1,
                    duration_seconds=8,
                    storyboards=ArtifactCount(total=1, available=1, stale=0),
                    videos=ArtifactCount(total=1, available=0, stale=0),
                )
            ],
        )


def _client(monkeypatch, fake_pm, fake_summaries=None):
    monkeypatch.setattr(projects, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(projects, "get_workflow_state_service", lambda: fake_summaries or _FakeSummaries())
    monkeypatch.setattr(projects, "get_script_batch_editor", lambda manager=None: _FakeBatchEditor(manager or fake_pm))

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(projects.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(projects.self_auth_router, prefix="/api/v1")
    register_error_handlers(app)
    return TestClient(app)
