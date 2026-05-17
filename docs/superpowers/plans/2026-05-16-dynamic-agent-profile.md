# Dynamic Agent Profile 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 project content_mode（narration / drama）裁剪 `agent_runtime_profile/CLAUDE.md` 和 `manga-workflow/SKILL.md` 两份始终加载文件，减少主 agent context 中无关分支。

**Architecture:** profile 端引入 `.narration.md` / `.drama.md` 同层后缀变体；`lib/profile_manifest.py` 新增 `resolve_profile_files_for_mode` 投影到逻辑路径，manifest 增可选 `content_mode` 字段（不 bump schema_version，避免破坏性 reset 回归）；`lib/project_manager.py` 三个公开 API 加 content_mode 参数；server 路由透传 `req.content_mode`。

**Tech Stack:** Python 3.12, pytest (asyncio_mode=auto), FastAPI, SQLAlchemy async, ruff (line-length 120)

**Spec:** `docs/superpowers/specs/2026-05-16-dynamic-agent-profile-design.md`

---

## 文件结构

**新增文件：**
- `agent_runtime_profile/CLAUDE.narration.md`
- `agent_runtime_profile/CLAUDE.drama.md`
- `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.narration.md`
- `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.drama.md`

**删除文件：**
- `agent_runtime_profile/CLAUDE.md`
- `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md`

**修改的后端文件：**
- `lib/profile_manifest.py` — 增 `ProfileMisconfiguredError`、`resolve_profile_files_for_mode`、`Manifest.content_mode`；改 `sync_profile_to_project`、`force_resync_profile`、`load_manifest`、`_apply_decision`、`_full_reset_from_profile` 签名
- `lib/project_manager.py` — `create_project` / `sync_agent_profile` / `force_resync_profile` / `sync_all_agent_profiles` 加 content_mode 处理
- `server/routers/projects.py:471` — 透传 `req.content_mode`

**修改的测试文件：**
- `tests/test_profile_manifest.py` — 新增 variant 解析、manifest 兼容、sync 端到端、ProjectManager 集成四组测试

---

## Task 1: 增加 `ProfileMisconfiguredError` + `_VALID_CONTENT_MODES` 常量

**Files:**
- Modify: `lib/profile_manifest.py:31-51`
- Test: `tests/test_profile_manifest.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_profile_manifest.py` 末尾新增：

```python
# ---------- ProfileMisconfiguredError ----------


def test_profile_misconfigured_error_is_runtime_error() -> None:
    """与 ProfileMissingError / ProfileEmptyError 同层级，都是部署级错误。"""
    from lib.profile_manifest import ProfileMisconfiguredError

    assert issubclass(ProfileMisconfiguredError, RuntimeError)


def test_valid_content_modes_constant() -> None:
    from lib.profile_manifest import _VALID_CONTENT_MODES

    assert _VALID_CONTENT_MODES == frozenset({"narration", "drama"})
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_profile_manifest.py::test_profile_misconfigured_error_is_runtime_error tests/test_profile_manifest.py::test_valid_content_modes_constant -v
```

Expected: FAIL with `ImportError` / `AttributeError`。

- [ ] **Step 3: 实现**

在 `lib/profile_manifest.py` 紧跟 `ProfileEmptyError` 之后添加：

```python
class ProfileMisconfiguredError(RuntimeError):
    """profile 端变体文件违反 §4.2 校验规则 → 部署错误。sync 拒绝运行。"""


_VALID_CONTENT_MODES = frozenset({"narration", "drama"})
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_profile_manifest.py::test_profile_misconfigured_error_is_runtime_error tests/test_profile_manifest.py::test_valid_content_modes_constant -v
```

Expected: PASS。

- [ ] **Step 5: lint + 提交**

```bash
uv run ruff check lib/profile_manifest.py tests/test_profile_manifest.py
uv run ruff format lib/profile_manifest.py tests/test_profile_manifest.py
git add lib/profile_manifest.py tests/test_profile_manifest.py
git commit -m "feat(profile): add ProfileMisconfiguredError + _VALID_CONTENT_MODES"
```

---

## Task 2: `resolve_profile_files_for_mode` — 变体投影核心算法

**Files:**
- Modify: `lib/profile_manifest.py` — 在 `enumerate_profile_files` 之后新增函数
- Test: `tests/test_profile_manifest.py`

- [ ] **Step 1: 写失败测试组**

在 `tests/test_profile_manifest.py` 添加（紧跟前面 enumerate 测试）：

```python
# ---------- resolve_profile_files_for_mode ----------


def _make_profile(tmp_path: Path) -> Path:
    """构造典型 profile：通用文件 + narration/drama 变体配对。"""
    profile = tmp_path / "profile"
    (profile / ".claude" / "skills" / "manga-workflow").mkdir(parents=True)
    (profile / ".claude" / "agents").mkdir(parents=True)
    # 通用文件
    (profile / ".claude" / "agents" / "generate-assets.md").write_text("common")
    # CLAUDE.md 变体配对
    (profile / "CLAUDE.narration.md").write_text("narration top")
    (profile / "CLAUDE.drama.md").write_text("drama top")
    # SKILL.md 变体配对
    (profile / ".claude" / "skills" / "manga-workflow" / "SKILL.narration.md").write_text("nar skill")
    (profile / ".claude" / "skills" / "manga-workflow" / "SKILL.drama.md").write_text("dra skill")
    return profile


def test_resolve_for_narration_picks_narration_variants(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "narration")

    assert mapping == {
        "CLAUDE.md": "CLAUDE.narration.md",
        ".claude/agents/generate-assets.md": ".claude/agents/generate-assets.md",
        ".claude/skills/manga-workflow/SKILL.md": ".claude/skills/manga-workflow/SKILL.narration.md",
    }


def test_resolve_for_drama_picks_drama_variants(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "drama")

    assert mapping[".claude/skills/manga-workflow/SKILL.md"] == ".claude/skills/manga-workflow/SKILL.drama.md"
    assert mapping["CLAUDE.md"] == "CLAUDE.drama.md"


def test_resolve_unpaired_variant_raises(tmp_path: Path) -> None:
    """只有 narration 变体没有 drama 变体 → ProfileMisconfiguredError。"""
    from lib.profile_manifest import ProfileMisconfiguredError, resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.narration.md").write_text("only narration")

    with pytest.raises(ProfileMisconfiguredError, match="missing variant"):
        resolve_profile_files_for_mode(profile, "narration")


def test_resolve_common_plus_variant_collision_raises(tmp_path: Path) -> None:
    """同一 logical_rel 既有通用文件又有变体 → ProfileMisconfiguredError。"""
    from lib.profile_manifest import ProfileMisconfiguredError, resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.md").write_text("common")
    (profile / "CLAUDE.narration.md").write_text("variant")
    (profile / "CLAUDE.drama.md").write_text("variant")

    with pytest.raises(ProfileMisconfiguredError, match="common.*variant"):
        resolve_profile_files_for_mode(profile, "narration")


def test_resolve_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    with pytest.raises(ValueError, match="content_mode"):
        resolve_profile_files_for_mode(profile, "reference_video")


def test_resolve_double_dot_filename_not_treated_as_variant(tmp_path: Path) -> None:
    """`foo.narration.bar.md` 不认作变体（只识别最后一段 stem）。"""
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    (profile / ".claude").mkdir(parents=True)
    (profile / ".claude" / "weird.narration.bar.md").write_text("not a variant")

    mapping = resolve_profile_files_for_mode(profile, "narration")
    assert mapping == {".claude/weird.narration.bar.md": ".claude/weird.narration.bar.md"}
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "resolve" -v
```

Expected: 全部 FAIL（`ImportError`）。

- [ ] **Step 3: 实现 `resolve_profile_files_for_mode`**

在 `lib/profile_manifest.py` 的 `enumerate_profile_files` 函数后追加：

```python
def _parse_variant_suffix(rel: str) -> tuple[str, str | None]:
    """把 ``foo.narration.md`` 拆成 (logical_rel="foo.md", mode="narration")。
    非变体文件返回 (rel, None)。只识别 stem 中最后一段。
    """
    path = PurePosixPath(rel)
    # path.stem 是去掉最后一个扩展名的部分；再 split 一次拿"次外层后缀"
    stem_parts = path.stem.rsplit(".", 1)
    if len(stem_parts) == 2 and stem_parts[1] in _VALID_CONTENT_MODES:
        logical_stem = stem_parts[0]
        # 重组为 logical_parent/<logical_stem><ext>
        logical_name = logical_stem + path.suffix
        if str(path.parent) in (".", ""):
            return logical_name, stem_parts[1]
        return (path.parent / logical_name).as_posix(), stem_parts[1]
    return rel, None


def resolve_profile_files_for_mode(
    profile_dir: Path,
    content_mode: str,
) -> dict[str, str]:
    """把 profile 端文件树投影成 ``{logical_rel: source_rel}`` 映射。

    通用文件：logical_rel == source_rel。
    变体文件：仅保留匹配 ``content_mode`` 的一份，logical_rel 去掉 ``.<mode>`` 后缀。

    Raises:
        ValueError: content_mode 不在 ``_VALID_CONTENT_MODES``
        ProfileMisconfiguredError: 任一变体配对缺失 / 通用+变体并存
    """
    if content_mode not in _VALID_CONTENT_MODES:
        raise ValueError(f"content_mode must be one of {_VALID_CONTENT_MODES}, got {content_mode!r}")

    profile_files = enumerate_profile_files(profile_dir)

    # variants[logical_rel][mode] = source_rel
    variants: dict[str, dict[str, str]] = {}
    commons: dict[str, str] = {}
    for src in profile_files:
        logical, mode = _parse_variant_suffix(src)
        if mode is None:
            commons[logical] = src
        else:
            variants.setdefault(logical, {})[mode] = src

    # 校验 1：通用 + 变体互斥
    collisions = set(commons) & set(variants)
    if collisions:
        sample = sorted(collisions)[0]
        raise ProfileMisconfiguredError(
            f"profile has both common and variant for {sample!r}; "
            f"remove one. all collisions: {sorted(collisions)}"
        )

    # 校验 2：变体配对完整
    for logical, by_mode in variants.items():
        missing = _VALID_CONTENT_MODES - set(by_mode)
        if missing:
            raise ProfileMisconfiguredError(
                f"profile variant {logical!r} missing variant for mode(s): {sorted(missing)}; "
                f"all variants of a logical file must exist together"
            )

    mapping: dict[str, str] = dict(commons)
    for logical, by_mode in variants.items():
        mapping[logical] = by_mode[content_mode]
    return mapping
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "resolve" -v
```

Expected: 6 个测试全部 PASS。

- [ ] **Step 5: lint + 提交**

```bash
uv run ruff check lib/profile_manifest.py tests/test_profile_manifest.py
uv run ruff format lib/profile_manifest.py tests/test_profile_manifest.py
git add lib/profile_manifest.py tests/test_profile_manifest.py
git commit -m "feat(profile): add resolve_profile_files_for_mode variant projection"
```

---

## Task 3: `Manifest` 数据类扩展可选 `content_mode` 字段

**Files:**
- Modify: `lib/profile_manifest.py:173-202` — `Manifest` 类
- Modify: `lib/profile_manifest.py:204-265` — `load_manifest` 函数
- Test: `tests/test_profile_manifest.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_profile_manifest.py` 增加测试（紧接 manifest 序列化测试组之后）：

```python
# ---------- Manifest.content_mode 字段 ----------


def test_manifest_empty_has_none_content_mode() -> None:
    m = Manifest.empty()
    assert m.content_mode is None


def test_manifest_serialize_omits_none_content_mode() -> None:
    """None content_mode 不出现在 JSON 中，保持向后兼容紧凑形态。"""
    m = Manifest.empty()
    data = json.loads(m.normalized_bytes().decode("utf-8"))
    assert "content_mode" not in data


def test_manifest_serialize_includes_set_content_mode() -> None:
    m = Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        profile_id=EXPECTED_PROFILE_ID,
        content_mode="narration",
        entries={},
    )
    data = json.loads(m.normalized_bytes().decode("utf-8"))
    assert data["content_mode"] == "narration"


def test_load_manifest_legacy_no_content_mode_field(tmp_path: Path) -> None:
    """老 manifest（无 content_mode 字段）→ load 成功，字段为 None。"""
    legacy = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(legacy))
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    manifest, _raw = loaded
    assert manifest.content_mode is None


def test_load_manifest_new_with_content_mode(tmp_path: Path) -> None:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "content_mode": "drama",
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload))
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    manifest, _raw = loaded
    assert manifest.content_mode == "drama"


def test_load_manifest_invalid_content_mode_returns_none(tmp_path: Path) -> None:
    """content_mode 字段存在但值非法 → 视为损坏，触发 reset。"""
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "content_mode": "garbage",
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(tmp_path) is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "content_mode or legacy_no_content_mode or new_with_content_mode or invalid_content_mode" -v
```

Expected: 6 个测试全部 FAIL。

- [ ] **Step 3: 改 `Manifest` 数据类**

`lib/profile_manifest.py` 中的 `Manifest` 类替换为：

```python
@dataclasses.dataclass
class Manifest:
    schema_version: int
    profile_id: str
    entries: dict[str, dict]
    content_mode: str | None = None

    @classmethod
    def empty(cls) -> Manifest:
        return cls(
            schema_version=MANIFEST_SCHEMA_VERSION,
            profile_id=EXPECTED_PROFILE_ID,
            entries={},
            content_mode=None,
        )

    def to_jsonable(self) -> dict:
        data: dict = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "entries": dict(sorted(self.entries.items())),
        }
        # None 时省略字段：兼容老 manifest 紧凑形态 + 减少 diff 噪音
        if self.content_mode is not None:
            data["content_mode"] = self.content_mode
        return data

    def normalized_bytes(self) -> bytes:
        return json.dumps(
            self.to_jsonable(),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")
```

- [ ] **Step 4: 改 `load_manifest` 处理 `content_mode`**

`lib/profile_manifest.py` 中 `load_manifest` 末尾 return 之前，新增 content_mode 解析：

定位到 `entries = data.get("entries")` 之后、entry 校验循环之前，插入：

```python
    raw_mode = data.get("content_mode")
    if raw_mode is None:
        content_mode: str | None = None
    elif isinstance(raw_mode, str) and raw_mode in _VALID_CONTENT_MODES:
        content_mode = raw_mode
    else:
        logger.warning("manifest %s invalid content_mode=%r, will reset", path, raw_mode)
        return None
```

然后把 return 部分改为：

```python
    return (
        Manifest(
            schema_version=data["schema_version"],
            profile_id=data["profile_id"],
            entries=entries,
            content_mode=content_mode,
        ),
        raw,
    )
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_profile_manifest.py -v
```

Expected: 全部 PASS（包括既有 manifest 序列化测试，因为 None 字段被省略，序列化输出无变化）。

- [ ] **Step 6: lint + 提交**

```bash
uv run ruff check lib/profile_manifest.py tests/test_profile_manifest.py
uv run ruff format lib/profile_manifest.py tests/test_profile_manifest.py
git add lib/profile_manifest.py tests/test_profile_manifest.py
git commit -m "feat(profile): add optional content_mode field to Manifest (no schema bump)"
```

---

## Task 4: `sync_profile_to_project` 加 `content_mode` 参数 + 投影逻辑

**Files:**
- Modify: `lib/profile_manifest.py:371-411` — `_full_reset_from_profile`
- Modify: `lib/profile_manifest.py:417-525` — `_apply_decision`
- Modify: `lib/profile_manifest.py:531-583` — `sync_profile_to_project`
- Test: `tests/test_profile_manifest.py`

- [ ] **Step 1: 写失败测试组**

在 `tests/test_profile_manifest.py` 末尾添加：

```python
# ---------- sync_profile_to_project 端到端 ----------


def _fresh_project(tmp_path: Path, name: str = "proj") -> Path:
    d = tmp_path / name
    d.mkdir()
    return d


def test_sync_narration_project_writes_narration_variant(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")

    assert (project / "CLAUDE.md").read_text() == "narration top"
    assert (project / ".claude" / "skills" / "manga-workflow" / "SKILL.md").read_text() == "nar skill"
    assert not (project / "CLAUDE.narration.md").exists()
    assert not (project / "CLAUDE.drama.md").exists()


def test_sync_drama_project_writes_drama_variant(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_sync_writes_manifest_content_mode(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")
    manifest_data = json.loads((project / MANIFEST_FILENAME).read_text())
    assert manifest_data["content_mode"] == "narration"


def test_sync_mode_mismatch_triggers_reset(tmp_path: Path) -> None:
    """已有 manifest 标记 narration，下次 sync 传 drama → reset 路径覆盖 dest。"""
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")
    assert (project / "CLAUDE.md").read_text() == "narration top"

    sync_profile_to_project(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_sync_legacy_manifest_migrates_without_reset(tmp_path: Path) -> None:
    """老 manifest（无 content_mode）+ 未改的 CLAUDE.md → 决策 #4 升级 + 写入 mode。"""
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    # 1) 先按 narration 物化一份（生成 manifest）
    sync_profile_to_project(profile, project, content_mode="narration")
    # 2) 手工把 manifest 改成"老 manifest"形态（删 content_mode 字段）
    manifest_path = project / MANIFEST_FILENAME
    data = json.loads(manifest_path.read_text())
    data.pop("content_mode", None)
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True))
    # 3) 再次 sync，应当被认作 needs_migration，正常走 #3 unchanged，写回 mode
    sync_profile_to_project(profile, project, content_mode="narration")
    after = json.loads(manifest_path.read_text())
    assert after["content_mode"] == "narration"
    # 内容不变
    assert (project / "CLAUDE.md").read_text() == "narration top"


def test_sync_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    with pytest.raises(ValueError, match="content_mode"):
        sync_profile_to_project(profile, project, content_mode="reference_video")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "test_sync_" -v
```

Expected: 全部 FAIL（签名缺 content_mode 参数 / 行为未实现）。

- [ ] **Step 3: 改 `_apply_decision` 接收 source_rel 参数**

`lib/profile_manifest.py` 中 `_apply_decision` 签名和内部路径替换：

```python
def _apply_decision(
    profile_dir: Path,
    project_dir: Path,
    rel: str,
    source_rel: str,
    p_exists: bool,
    d_exists: bool,
    m: dict | None,
    manifest: Manifest,
    stats: dict,
) -> None:
```

函数体内 `p = profile_dir / rel` 改为 `p = profile_dir / source_rel`；其它 `rel` 用法（dest 路径 + manifest key）保持不变。

- [ ] **Step 4: 改 `_full_reset_from_profile` 接收 mapping**

签名改为：

```python
def _full_reset_from_profile(
    profile_dir: Path,
    project_dir: Path,
    mapping: dict[str, str],
) -> dict:
```

内部循环改为：

```python
    for rel in sorted(mapping):
        source_rel = mapping[rel]
        source = profile_dir / source_rel
        try:
            _safe_copy(source, project_dir / rel)
            sha = sha256_file(source)
            size = source.stat().st_size
            manifest.entries[rel] = _profile_active_entry(sha, size)
            stats["migrated_total"] += 1
            stats["created"] += 1
        except OSError as e:
            logger.warning("profile reset skip %s: %s", rel, e)
            stats["errors"] += 1
```

- [ ] **Step 5: 改 `sync_profile_to_project` 签名 + 主体**

替换为：

```python
def sync_profile_to_project(
    profile_dir: Path,
    project_dir: Path,
    content_mode: str,
) -> dict:
    """profile → project_dir 同步主入口。

    Raises:
        ProfileMissingError: profile 目录不存在
        ProfileEmptyError: profile 目录无可同步文件
        ProfileMisconfiguredError: 变体文件配置违规
        ValueError: content_mode 非 narration/drama
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)

    with _project_lock(project_dir):
        loaded = load_manifest(project_dir)
        if loaded is None:
            return _full_reset_from_profile(profile_dir, project_dir, mapping)
        manifest, original_bytes = loaded

        # mode_status 判定：missing(None)=needs_migration，存在但不匹配=mismatch → reset
        if manifest.content_mode is not None and manifest.content_mode != content_mode:
            logger.info(
                "manifest %s content_mode %r != requested %r, resetting",
                project_dir / MANIFEST_FILENAME,
                manifest.content_mode,
                content_mode,
            )
            return _full_reset_from_profile(profile_dir, project_dir, mapping)

        stats = _new_stats()
        dest_files = enumerate_dest_files(project_dir)
        all_keys = set(mapping) | dest_files | set(manifest.entries.keys())

        for rel in sorted(all_keys):
            p_exists = rel in mapping
            d_exists = rel in dest_files
            m = manifest.entries.get(rel)
            try:
                _ensure_dest_within(project_dir, rel)
                _apply_decision(
                    profile_dir,
                    project_dir,
                    rel,
                    mapping.get(rel, rel),  # source_rel；rel 不在 mapping 时（p 不存在）用 rel 占位
                    p_exists,
                    d_exists,
                    m,
                    manifest,
                    stats,
                )
            except ValueError as e:
                logger.warning("profile sync skip %s (escape guard): %s", rel, e)
                stats["errors"] += 1
            except OSError as e:
                logger.warning("profile sync skip %s: %s", rel, e)
                stats["errors"] += 1

        # sync 主体完成后写入 mode（无论 needs_migration 还是 match）
        manifest.content_mode = content_mode
        save_manifest(project_dir, manifest, original_bytes)
        return stats
```

- [ ] **Step 6: 临时让 ProjectManager 老 caller 不破坏（Task 6 会真正修）**

`lib/project_manager.py:200` 把：

```python
return sync_profile_to_project(profile_dir, project_dir)
```

改为：

```python
return sync_profile_to_project(profile_dir, project_dir, "narration")
```

`lib/project_manager.py:208`（`force_resync_profile`）同理把：

```python
return _force_resync_profile(profile_dir, project_dir, paths=paths)
```

改为：

```python
return _force_resync_profile(profile_dir, project_dir, "narration", paths=paths)
```

> 这两个硬编码 `"narration"` 是临时支撑，Task 6 用 project.json 读取替换。

- [ ] **Step 7: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_profile_manifest.py -v
```

Expected: 全部 PASS。

```bash
uv run python -m pytest tests/test_project_manager_compat.py tests/test_project_manager_symlink.py -v
```

Expected: 全部 PASS（依赖 ProjectManager 的集成测试因为硬编码 narration 临时兜底而通过）。

- [ ] **Step 8: lint + 提交**

```bash
uv run ruff check lib/profile_manifest.py lib/project_manager.py tests/test_profile_manifest.py
uv run ruff format lib/profile_manifest.py lib/project_manager.py tests/test_profile_manifest.py
git add lib/profile_manifest.py lib/project_manager.py tests/test_profile_manifest.py
git commit -m "feat(profile): make sync_profile_to_project content_mode-aware"
```

---

## Task 5: `force_resync_profile` 加 `content_mode` 参数

**Files:**
- Modify: `lib/profile_manifest.py:586-650` — `force_resync_profile`
- Test: `tests/test_profile_manifest.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_profile_manifest.py` 末尾添加：

```python
# ---------- force_resync_profile ----------


def test_force_resync_picks_correct_variant(tmp_path: Path) -> None:
    """传逻辑路径 'CLAUDE.md'，按 mode 选对应变体源文件。"""
    from lib.profile_manifest import force_resync_profile, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")

    # 用户手动改 CLAUDE.md
    (project / "CLAUDE.md").write_text("user-edited")

    # force_resync 应当用 narration 变体覆盖
    force_resync_profile(profile, project, content_mode="narration", paths=["CLAUDE.md"])
    assert (project / "CLAUDE.md").read_text() == "narration top"


def test_force_resync_full_uses_mapping(tmp_path: Path) -> None:
    """paths=None 全量恢复时也走变体投影。"""
    from lib.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    force_resync_profile(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_force_resync_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    with pytest.raises(ValueError, match="content_mode"):
        force_resync_profile(profile, project, content_mode="bad")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "force_resync" -v
```

Expected: FAIL（签名缺 content_mode）。

- [ ] **Step 3: 改 `force_resync_profile`**

`lib/profile_manifest.py` 中替换为：

```python
def force_resync_profile(
    profile_dir: Path,
    project_dir: Path,
    content_mode: str,
    *,
    paths: Iterable[str] | None = None,
) -> dict:
    """强制按 P 覆盖 D 并更新 manifest，清除 tombstone。

    给 UI"恢复内置 skill"按钮使用。``paths=None`` 表示全量。
    ``paths`` 是**逻辑路径**（如 ``CLAUDE.md``），内部按 content_mode 查源路径。
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    if paths is not None:
        target = {_normalize_profile_rel_path(rel) for rel in paths}
    else:
        target = set(mapping)

    project_dir.mkdir(parents=True, exist_ok=True)

    with _project_lock(project_dir):
        loaded = load_manifest(project_dir)
        if loaded is None:
            if paths is None:
                return _full_reset_from_profile(profile_dir, project_dir, mapping)
            manifest, original_bytes = Manifest.empty(), None
        else:
            manifest, original_bytes = loaded

        stats = _new_stats()
        for rel in sorted(target):
            source_rel = mapping.get(rel)
            if source_rel is None:
                logger.warning("force_resync skip missing profile file: %s", rel)
                continue
            p = profile_dir / source_rel
            if not p.is_file():
                logger.warning("force_resync skip missing profile file: %s", rel)
                continue
            try:
                _ensure_dest_within(project_dir, rel)
                d = project_dir / rel
                _safe_copy(p, d)
                sha = sha256_file(p)
                manifest.entries[rel] = _profile_active_entry(sha, p.stat().st_size)
                stats["created"] += 1
                stats["repaired"] += 1
            except ValueError as e:
                logger.warning("force_resync skip %s (escape guard): %s", rel, e)
                stats["errors"] += 1
            except OSError as e:
                logger.warning("force_resync skip %s: %s", rel, e)
                stats["errors"] += 1

        manifest.content_mode = content_mode
        save_manifest(project_dir, manifest, original_bytes)
        return stats
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "force_resync" -v
```

Expected: 3 个测试全部 PASS。

- [ ] **Step 5: lint + 提交**

```bash
uv run ruff check lib/profile_manifest.py tests/test_profile_manifest.py
uv run ruff format lib/profile_manifest.py tests/test_profile_manifest.py
git add lib/profile_manifest.py tests/test_profile_manifest.py
git commit -m "feat(profile): make force_resync_profile content_mode-aware"
```

---

## Task 6: `ProjectManager` 集成 — 公开 API 接收 content_mode

**Files:**
- Modify: `lib/project_manager.py:159-186` — `create_project`
- Modify: `lib/project_manager.py:188-209` — `sync_agent_profile` / `force_resync_profile`
- Modify: `lib/project_manager.py:211-280` — `sync_all_agent_profiles`
- Test: `tests/test_profile_manifest.py`（新建 ProjectManager 端到端组）

- [ ] **Step 1: 写失败测试组**

在 `tests/test_profile_manifest.py` 末尾添加 ProjectManager 集成测试：

```python
# ---------- ProjectManager 集成 ----------


def _setup_pm_with_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    """构造 ProjectManager + 指向 _make_profile 生成的 profile 目录。"""
    from lib import project_manager as pm_module

    profile = _make_profile(tmp_path)
    monkeypatch.setattr(pm_module, "agent_profile_dir", lambda: profile)
    pm = pm_module.ProjectManager(projects_root=str(tmp_path / "projects"))
    return pm, profile


def test_create_project_with_drama_mode_materializes_drama_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="drama")
    assert (project_dir / "CLAUDE.md").read_text() == "drama top"
    assert (
        project_dir / ".claude" / "skills" / "manga-workflow" / "SKILL.md"
    ).read_text() == "dra skill"


def test_create_project_default_is_narration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """老 caller 不传 content_mode → 默认 narration（与产品默认一致）。"""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo")
    assert (project_dir / "CLAUDE.md").read_text() == "narration top"


def test_sync_agent_profile_reads_content_mode_from_project_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    # 改 project.json 模拟"老项目缺 mode 字段"
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo", "content_mode": "drama"}))
    # 再次 sync，应当读 project.json 拿到 drama，触发 mode mismatch reset
    pm.sync_agent_profile(project_dir)
    assert (project_dir / "CLAUDE.md").read_text() == "drama top"


def test_sync_agent_profile_missing_mode_fallback_narration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    # 模拟老项目：project.json 没有 content_mode 字段
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo"}))
    pm.sync_agent_profile(project_dir)
    # 回退 narration，内容不变
    assert (project_dir / "CLAUDE.md").read_text() == "narration top"


def test_sync_agent_profile_invalid_mode_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo", "content_mode": "garbage"}))
    with pytest.raises(ValueError, match="content_mode"):
        pm.sync_agent_profile(project_dir)


def test_sync_all_agent_profiles_per_project_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    pm.create_project("a", content_mode="narration")
    pm.create_project("b", content_mode="drama")
    # 改两个项目的内容（模拟 server 启动前 profile 已升级）
    stats = pm.sync_all_agent_profiles()
    assert stats.get("aborted") is not True
    assert (pm.projects_root / "a" / "CLAUDE.md").read_text() == "narration top"
    assert (pm.projects_root / "b" / "CLAUDE.md").read_text() == "drama top"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run python -m pytest tests/test_profile_manifest.py -k "create_project_with_drama or default_is_narration or sync_agent_profile or sync_all_agent_profiles_per_project" -v
```

Expected: FAIL（缺 content_mode 参数 / 行为未实现）。

- [ ] **Step 3: 改 `create_project`**

`lib/project_manager.py:159` 处签名 + 主体改为：

```python
    def create_project(self, name: str, content_mode: str = "narration") -> Path:
        """
        创建新项目

        Args:
            name: 项目标识（全局唯一，用于 URL 和文件系统）
            content_mode: 内容模式（narration / drama），影响 profile 物化时选哪份变体

        Returns:
            项目目录路径
        """
        name = self.normalize_project_name(name)
        project_dir = self.projects_root / name

        if project_dir.exists():
            raise FileExistsError(f"项目 '{name}' 已存在")

        for subdir in self.SUBDIRS:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)

        try:
            self.sync_agent_profile(project_dir, content_mode=content_mode)
        except Exception:
            shutil.rmtree(project_dir, ignore_errors=True)
            raise

        return project_dir
```

- [ ] **Step 4: 改 `sync_agent_profile` / `force_resync_profile`**

`lib/project_manager.py:188` 处替换为：

```python
    def sync_agent_profile(
        self,
        project_dir: Path,
        *,
        content_mode: str | None = None,
    ) -> dict:
        """同步 agent_runtime_profile 到项目目录的 .claude / CLAUDE.md。

        ``content_mode=None`` 时从 ``project_dir/project.json`` 读取；
        project.json 缺失或 ``content_mode`` 字段缺失 → 回退到 ``"narration"`` + log info。
        ``content_mode`` 显式非法值 → 抛 ``ValueError``。
        """
        if content_mode is None:
            content_mode = self._resolve_content_mode(project_dir)
        profile_dir = agent_profile_dir()
        return sync_profile_to_project(profile_dir, project_dir, content_mode)

    def force_resync_profile(
        self,
        project_dir: Path,
        *,
        paths: list[str] | None = None,
        content_mode: str | None = None,
    ) -> dict:
        """强制按 profile 覆盖项目内对应文件并刷新 manifest。"""
        if content_mode is None:
            content_mode = self._resolve_content_mode(project_dir)
        profile_dir = agent_profile_dir()
        return _force_resync_profile(profile_dir, project_dir, content_mode, paths=paths)

    def _resolve_content_mode(self, project_dir: Path) -> str:
        """从 project_dir/project.json 读 content_mode；缺失回退 narration。

        project.json 不存在或缺 content_mode 字段 → 回退 narration（兼容老项目）。
        文件存在但读取/解析失败 → raise，让上层 sync_all_agent_profiles 走
        failed_projects 分支；静默回退会导致 drama 项目 manifest mode 不匹配
        触发破坏性 reset，把 profile 错切回说书变体。
        """
        pj_path = project_dir / self.PROJECT_FILE
        try:
            data = json.loads(pj_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.info("project.json missing under %s, defaulting content_mode=narration", project_dir)
            return "narration"
        # 注意：OSError / JSONDecodeError 不在这里 catch，自然抛出让 sync_all_agent_profiles
        # 走 failed_projects 分支，避免静默回退到 narration 后触发 destructive reset。
        mode = data.get("content_mode")
        if mode is None:
            logger.info("project.json missing content_mode under %s, defaulting narration", project_dir)
            return "narration"
        if not isinstance(mode, str) or mode not in {"narration", "drama"}:
            raise ValueError(
                f"project {project_dir.name}: invalid content_mode={mode!r} (must be narration or drama)"
            )
        return mode
```

- [ ] **Step 5: 改 `sync_all_agent_profiles` 失败模式**

`lib/project_manager.py:211` 处主循环 try 块改为捕获 `ValueError` 单独计数（非法 mode → 跳过 + 警告，不算部署级 abort）：

定位到 `try:\n    result = self.sync_agent_profile(project_dir)` 这一行，把 `except` 部分调整为：

```python
            try:
                result = self.sync_agent_profile(project_dir)
                for key in _STAT_KEYS_TO_AGGREGATE:
                    if key in result:
                        totals[key] = totals.get(key, 0) + result[key]
            except (ProfileMissingError, ProfileEmptyError, ProfileMisconfiguredError) as e:
                # 部署级错误 → 全部跳过
                logger.error("Profile deploy misconfig, aborting all sync: %s", e)
                totals["aborted"] = True
                return totals
            except ValueError as e:
                # 单个项目 content_mode 非法 → 跳过，不影响其它项目
                logger.warning("Skip sync for %s: %s", project_dir.name, e)
                totals["failed_projects"] += 1
            except Exception:
                logger.exception("Unexpected error syncing %s", project_dir)
                totals["failed_projects"] += 1
```

并在 `lib/project_manager.py:26-33` 的 import 加上 `ProfileMisconfiguredError`：

```python
from lib.profile_manifest import (
    ProfileEmptyError,
    ProfileMisconfiguredError,
    ProfileMissingError,
    sync_profile_to_project,
)
```

- [ ] **Step 6: 运行新测试确认通过**

```bash
uv run python -m pytest tests/test_profile_manifest.py -v
```

Expected: 全部 PASS。

- [ ] **Step 7: 跑现存依赖测试，修复签名链式破坏**

```bash
uv run python -m pytest tests/test_project_manager_compat.py tests/test_project_manager_more.py tests/test_project_manager_symlink.py tests/test_project_manager_concurrent_save.py tests/test_project_manager_migration.py tests/test_project_manager_legacy_migration.py -v
```

修复逻辑：所有调用 `pm.create_project("x")` 的 case 默认 narration 即可；调用 `pm.sync_agent_profile(project_dir)` 的 case 自动从 project.json 读 mode，若项目已先 `create_project_metadata` 写过 content_mode 则无需修改。

如有 test 显式打桩 `sync_profile_to_project` 接受老签名，需要更新到新签名。检查项：

```bash
grep -n "sync_profile_to_project" tests/
```

逐个对照新签名 `(profile_dir, project_dir, content_mode)` 修正。

- [ ] **Step 8: 再跑全套相关测试**

```bash
uv run python -m pytest tests/test_profile_manifest.py tests/test_project_manager_compat.py tests/test_project_manager_more.py tests/test_project_manager_symlink.py tests/test_project_manager_concurrent_save.py tests/test_project_manager_migration.py tests/test_project_manager_legacy_migration.py -v
```

Expected: 全部 PASS。

- [ ] **Step 9: lint + 提交**

```bash
uv run ruff check lib/profile_manifest.py lib/project_manager.py tests/test_profile_manifest.py
uv run ruff format lib/profile_manifest.py lib/project_manager.py tests/test_profile_manifest.py
git add lib/profile_manifest.py lib/project_manager.py tests/test_profile_manifest.py
git commit -m "feat(profile): ProjectManager content_mode integration"
```

---

## Task 7: server router 透传 `req.content_mode`

**Files:**
- Modify: `server/routers/projects.py:471`
- Test: `tests/test_projects_router.py`（新增 case）

- [ ] **Step 1: 写失败测试**

定位 `tests/test_projects_router.py` 现有的"创建项目"测试，在其后新增：

```python
def test_create_project_drama_mode_materializes_drama_variant(client, tmp_path) -> None:
    """POST /projects content_mode=drama → project_dir 内 CLAUDE.md 用 drama 变体。

    依赖 ``conftest.py`` 中 monkeypatched ``agent_profile_dir`` 指向真实 profile 目录。
    """
    resp = client.post(
        "/api/v1/projects",
        json={"title": "test-drama", "content_mode": "drama"},
    )
    assert resp.status_code == 200
    name = resp.json()["name"]
    project_dir = pathlib.Path(resp.json()["project"]["project_root"])
    # 由于真实 profile 端 SKILL.drama.md 存在（Task 9 提供）才能 assert 文本
    # 这里先 assert 文件存在性，不 assert 文本（避开 Task 排序）
    assert (project_dir / "CLAUDE.md").is_file()
```

> 注：如果当前 `test_projects_router.py` 已经覆盖了 content_mode 透传逻辑（grep 一下），跳过新增。

```bash
grep -n "content_mode" tests/test_projects_router.py
```

如果已有断言 `content_mode == "drama"` 入库的 case，本 Task 不需新增测试，只需修一行代码。

- [ ] **Step 2: 改 router**

`server/routers/projects.py:471` 把：

```python
                manager.create_project(project_name)
```

改为：

```python
                manager.create_project(project_name, content_mode=req.content_mode or "narration")
```

- [ ] **Step 3: 跑 router 测试**

```bash
uv run python -m pytest tests/test_projects_router.py -v
```

Expected: PASS。

- [ ] **Step 4: lint + 提交**

```bash
uv run ruff check server/routers/projects.py
uv run ruff format server/routers/projects.py
git add server/routers/projects.py tests/test_projects_router.py
git commit -m "feat(projects): pass content_mode to create_project for variant sync"
```

---

## Task 8: 拆分 `agent_runtime_profile/CLAUDE.md` 为 narration / drama 两份变体

**Files:**
- Create: `agent_runtime_profile/CLAUDE.narration.md`
- Create: `agent_runtime_profile/CLAUDE.drama.md`
- Delete: `agent_runtime_profile/CLAUDE.md`

> 这一 Task 没有自动化测试——内容性变更。验证靠 Task 10 的整合 smoke。

- [ ] **Step 1: 读现有 CLAUDE.md 全文**

```bash
cat agent_runtime_profile/CLAUDE.md
```

记下文件结构（章节、段落）。

- [ ] **Step 2: 写 `agent_runtime_profile/CLAUDE.narration.md`**

复制现有 `CLAUDE.md` 内容到 `CLAUDE.narration.md`，做以下改动：

1. 文件开头第一行插入 HTML 注释 `<!-- mode: narration -->`（紧跟 `# AI 视频生成工作空间` 标题之后）

2. "## 重要总则 / ### 视频规格" 段：把

```
- **视频比例**：由项目 `aspect_ratio` 配置决定，无需在 prompt 中指定
  - 说书+画面模式默认：**9:16 竖屏**
  - 剧集动画模式默认：16:9 横屏
```

改为：

```
- **视频比例**：由项目 `aspect_ratio` 配置决定，无需在 prompt 中指定
```

（删去两个 mode 默认值的子项，画面比例由 aspect_ratio 独立决定）

3. "### 视频规格" 段 `- **单片段/场景时长**` 子项，删去说书+画面 / 剧集动画的并列说明，只保留：

```
- **单片段/场景时长**：由视频模型能力和项目 `default_duration` 配置决定
  - storyboard / grid 模式：由项目 `default_duration` 决定
  - reference_video 模式：由所选视频模型的 `supported_durations` 决定；subagent 运行时通过 `mcp__arcreel__get_video_capabilities` 工具自查真值
```

4. "## 内容模式" 段替换为：

```
## 内容模式

本项目为**说书+画面模式**（narration）。剧本数据结构为 `segments[]`，每个片段对应一段朗读 + 一张分镜画面。

> 生成模式（storyboard / grid / reference_video）通过 `project.json` 的 `generation_mode` 字段配置，与内容模式独立。详细规格见 `.claude/references/generation-modes.md`。
```

5. 其余段落（生成模式、项目结构、架构、可用 Skills、快速开始、工作流程概览、关键原则、项目目录结构、project.json 核心字段、数据分层原则）**保持不变**。

- [ ] **Step 3: 写 `agent_runtime_profile/CLAUDE.drama.md`**

操作同 Step 2，但：

- 头部注释改为 `<!-- mode: drama -->`
- "## 内容模式" 段替换为：

```
## 内容模式

本项目为**剧集动画模式**（drama）。剧本数据结构为 `scenes[]`，每个场景对应一段独立的视觉画面（含对话、动作、情绪）。

> 生成模式（storyboard / grid / reference_video）通过 `project.json` 的 `generation_mode` 字段配置，与内容模式独立。详细规格见 `.claude/references/generation-modes.md`。
```

其它修改点（画面比例、单片段时长、其它段保持）与 narration 变体一致。

- [ ] **Step 4: 删除老 `CLAUDE.md`**

```bash
git rm agent_runtime_profile/CLAUDE.md
```

- [ ] **Step 5: 校验 profile 端通过 `resolve_profile_files_for_mode` 解析**

```bash
uv run python -c "
from pathlib import Path
from lib.profile_manifest import resolve_profile_files_for_mode
profile = Path('agent_runtime_profile')
mapping_n = resolve_profile_files_for_mode(profile, 'narration')
mapping_d = resolve_profile_files_for_mode(profile, 'drama')
assert mapping_n['CLAUDE.md'] == 'CLAUDE.narration.md'
assert mapping_d['CLAUDE.md'] == 'CLAUDE.drama.md'
print('OK')
"
```

Expected: 输出 `OK`。

- [ ] **Step 6: 提交**

```bash
git add agent_runtime_profile/CLAUDE.narration.md agent_runtime_profile/CLAUDE.drama.md
git commit -m "docs(profile): split CLAUDE.md into narration/drama variants"
```

---

## Task 9: 拆分 `manga-workflow/SKILL.md` 为 narration / drama 两份变体

**Files:**
- Create: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.narration.md`
- Create: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.drama.md`
- Delete: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md`

- [ ] **Step 1: 读现有 SKILL.md 全文**

```bash
cat agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
```

- [ ] **Step 2: 写 `SKILL.narration.md`**

复制现有内容，做以下改动：

1. frontmatter 之后插入 `<!-- mode: narration -->`

2. "## 阶段 0：项目设置 / ### 新项目" 第 4 步：把

```
4. **询问内容模式**：`narration`（默认）或 `drama`
```

改为：

```
4. **内容模式**：本项目已固定为 `content_mode=narration`（创建后不可变更）
```

3. "## 状态检测" 第 3 条：从

```
3. 目标集 drafts/ 中间文件不存在？ → **阶段 3**
   - narration（generation_mode ∈ {storyboard, grid}）: `drafts/episode_{N}/step1_segments.md`
   - drama（generation_mode ∈ {storyboard, grid}）: `drafts/episode_{N}/step1_normalized_script.md`
   - reference_video: `drafts/episode_{N}/step1_reference_units.md`
```

改为：

```
3. 目标集 drafts/ 中间文件不存在？ → **阶段 3**
   - generation_mode ∈ {storyboard, grid}: `drafts/episode_{N}/step1_segments.md`
   - generation_mode == reference_video: `drafts/episode_{N}/step1_reference_units.md`
```

（删去 drama 行）

4. "## 阶段 3：单集预处理" 主体替换为：

```
**触发**：目标集的 drafts/ 中间文件不存在

根据 `effective_mode(project, episode)` 选择 subagent：

- `generation_mode == reference_video` → dispatch `split-reference-video-units`
- 否则 → dispatch `split-narration-segments`

dispatch prompt 通用参数：项目名称、项目路径、集数、本集小说文件路径。

（两个预处理 subagent 会自行读 project.json + 调用
`mcp__arcreel__get_video_capabilities({})`
拿到模型能力与用户偏好；主 agent 不需要预先注入角色/场景/道具列表或
`supported_durations` / `max_duration` / `max_reference_images` / `default_duration` 等数据。）
```

5. 其余段落保持不变。

- [ ] **Step 3: 写 `SKILL.drama.md`**

操作同 Step 2，但：

1. frontmatter 之后插入 `<!-- mode: drama -->`

2. "## 阶段 0 / ### 新项目" 第 4 步：

```
4. **内容模式**：本项目已固定为 `content_mode=drama`（创建后不可变更）
```

3. "## 状态检测" 第 3 条：

```
3. 目标集 drafts/ 中间文件不存在？ → **阶段 3**
   - generation_mode ∈ {storyboard, grid}: `drafts/episode_{N}/step1_normalized_script.md`
   - generation_mode == reference_video: `drafts/episode_{N}/step1_reference_units.md`
```

4. "## 阶段 3" 主体：

```
**触发**：目标集的 drafts/ 中间文件不存在

根据 `effective_mode(project, episode)` 选择 subagent：

- `generation_mode == reference_video` → dispatch `split-reference-video-units`
- 否则 → dispatch `normalize-drama-script`

dispatch prompt 通用参数：项目名称、项目路径、集数、本集小说文件路径。
```

- [ ] **Step 4: 删除老 SKILL.md**

```bash
git rm agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
```

- [ ] **Step 5: 校验 profile 端解析正确**

```bash
uv run python -c "
from pathlib import Path
from lib.profile_manifest import resolve_profile_files_for_mode
profile = Path('agent_runtime_profile')
mapping = resolve_profile_files_for_mode(profile, 'narration')
key = '.claude/skills/manga-workflow/SKILL.md'
assert mapping[key] == '.claude/skills/manga-workflow/SKILL.narration.md', mapping[key]
print('OK')
"
```

Expected: `OK`。

- [ ] **Step 6: 提交**

```bash
git add agent_runtime_profile/.claude/skills/manga-workflow/SKILL.narration.md agent_runtime_profile/.claude/skills/manga-workflow/SKILL.drama.md
git commit -m "docs(profile): split manga-workflow SKILL.md into narration/drama variants"
```

---

## Task 10: 整合 smoke — 全套测试 + 启动 server 验证 sync

**Files:** 无代码变更，仅运行验证。

- [ ] **Step 1: 跑全套测试**

```bash
uv run python -m pytest tests/ -v
```

Expected: 全绿。若有失败，定位到对应 task 修复，不要在本 task 内修。

- [ ] **Step 2: 跑覆盖率（确认 80% 阈值）**

```bash
uv run python -m pytest tests/test_profile_manifest.py tests/test_project_manager_compat.py tests/test_project_manager_more.py --cov=lib.profile_manifest --cov=lib.project_manager --cov-report=term-missing
```

确认 `lib/profile_manifest.py` 覆盖率 ≥80%（profile_manifest 已是高覆盖模块，应能维持）。

- [ ] **Step 3: 启动 server，让 sync_all_agent_profiles 跑一遍**

新开 terminal：

```bash
uv run uvicorn server.app:app --reload-dir server --reload-dir lib --port 1241
```

观察启动日志中 `_summarize_profile_sync_stats` 输出（`server/app.py:244`）：
- `created` / `upgraded` 计数应非零（首次部署 narration / drama 变体后所有项目都会触发决策 #4 升级）
- `aborted` 应为 `False`
- `failed_projects` 应为 0

按 Ctrl+C 停止 server。

- [ ] **Step 4: 抽查一个已有项目**

```bash
ls projects/
# 选一个项目，假设 projects/some-proj/
PROJ=$(ls projects/ | grep -v '^_' | grep -v '^\.' | head -1)
echo "Inspecting: $PROJ"
head -30 "projects/$PROJ/CLAUDE.md"
cat "projects/$PROJ/.arcreel_profile_manifest.json" | python -c "import json,sys; d=json.load(sys.stdin); print('content_mode:', d.get('content_mode')); print('schema_version:', d.get('schema_version'))"
```

Expected：
- `CLAUDE.md` 内容与对应 mode 变体匹配（narration 项目里没有 drama 描述）
- manifest 文件内 `content_mode` 字段存在且与 `project.json` 一致
- `schema_version: 1`（未 bump）

- [ ] **Step 5: ruff 终检 + 提交（如本 task 产生 fmt 调整）**

```bash
uv run ruff check . 2>&1 | tail -10
uv run ruff format --check . 2>&1 | tail -10
```

Expected: 无 issues。

如有 lint warning 修复后：

```bash
git add -p   # 选择性 stage
git commit -m "chore(profile): finalize dynamic agent profile (lint pass)"
```

如果完全无变更，跳过本 commit。

---

## 自检备忘

实施过程中若出现以下情况，**停下来对照 spec**：

1. `_apply_decision` 出现 dest 写入 `.narration.md` 后缀的文件 → 说明 source_rel / logical_rel 用反了
2. 老 manifest（无 content_mode）首次 sync 后所有内容被 reset 清空 → 说明 needs_migration 走了 mismatch 路径，检查 §5.4 判定条件
3. `force_resync_profile` 传 `paths=["CLAUDE.narration.md"]` 报错 → 这是预期：UI / API 只接受逻辑路径，不能传变体源路径

最终交付：
- `lib/profile_manifest.py` / `lib/project_manager.py` 加 content_mode 支持
- `server/routers/projects.py` 透传
- profile 端 4 份变体文件 + 2 个老文件已删
- 测试 + ruff 通过
