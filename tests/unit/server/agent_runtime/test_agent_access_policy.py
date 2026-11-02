"""AgentAccessPolicy 纯规则测试：构造参数喂入，断言三态 Decision，无 env/私有方法 monkeypatch。

路径裁决三态（见 Decision）：
- ALLOW 直接放行：cwd 内 / 用户记忆 / 仓库根参考资料 / 合法写 / SDK tool-results 例外。
- ASK 弹窗请求授权：数据根外敏感文件、跨项目读、数据根内其他条目、搜索根含数据根、host 路径、cwd 外写、
  受保护写路径（PROTECTED_WRITE_RULES）、代码扩展名写入。
- DENY 硬拒：无效路径（坏输入）。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from lib.agent.agent_memory_paths import project_memory_dir
from lib.infra.data_root_layout import DataRootLayout
from server.agent_runtime.agent_access_policy import AgentAccessPolicy, Decision

#: 逐调用传入的当前用户 id（生产取自 SessionManager 的 CurrentUser 上下文）。
_USER_ID = "default"


def _make_policy(tmp_path: Path, **overrides: object) -> AgentAccessPolicy:
    """以 tmp 根路径纯构造 policy：repo 布局与旧 SessionManager fixture 一致。"""
    project_root = (tmp_path / "repo").resolve()
    kwargs: dict[str, object] = {
        "project_root": project_root,
        "data_root": project_root / "projects",
        "agent_profile_root": (tmp_path / "agent_runtime_profile").resolve(),
    }
    kwargs.update(overrides)
    return AgentAccessPolicy(**kwargs)


@pytest.fixture
def policy(tmp_path: Path) -> AgentAccessPolicy:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    policy = _make_policy(tmp_path)
    (_projects_dir(policy) / "selfproj").mkdir(parents=True)
    (_projects_dir(policy) / "other").mkdir()
    (project_root / "lib").mkdir()
    return policy


def _projects_dir(policy: AgentAccessPolicy) -> Path:
    return DataRootLayout(policy.data_root).projects_dir


def _cwd(policy: AgentAccessPolicy) -> Path:
    return _projects_dir(policy) / "selfproj"


# ============================================================
# 构造纯度：假根路径可构造，不 import SDK
# ============================================================


def test_pure_construction_with_fake_roots() -> None:
    """假根路径（磁盘上不存在）+ sandbox_enabled 即可纯构造，裁决照常工作。"""
    fake = Path("/nonexistent/fake-root")
    policy = AgentAccessPolicy(
        project_root=fake / "repo",
        data_root=fake / "repo" / "projects",
        agent_profile_root=fake / "profile",
        sandbox_enabled=False,
        claude_projects_dir=fake / "claude" / "projects",
    )
    assert policy.sandbox_enabled is False
    cwd = DataRootLayout(fake / "repo" / "projects").projects_dir / "demo"
    decision, _ = policy.check_path_access(str(cwd / "data.json"), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW
    decision, reason = policy.check_path_access(str(fake / "repo" / ".env"), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ASK
    assert reason
    assert "敏感文件" in reason


def test_policy_module_does_not_import_sdk_types() -> None:
    """规则真相源不 import SDK 类型——SDK 封皮（权限结果类型、hook 签名）留在 adapter。"""
    module = sys.modules[AgentAccessPolicy.__module__]
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in source


# ============================================================
# 路径读写裁决（原 test_path_isolation_hook.py 断言搬家）
# ============================================================


def test_read_cwd_internal_passes(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    decision, _ = policy.check_path_access(str(cwd / "data.json"), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW


def test_read_other_project_asks(policy: AgentAccessPolicy) -> None:
    """跨项目读改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(
        str(_projects_dir(policy) / "other" / "x.json"), "Read", cwd, user_id=_USER_ID
    )
    assert decision is Decision.ASK
    assert "跨项目" in reason or "项目" in reason


@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_invalid_path_denies(policy: AgentAccessPolicy, tool: str) -> None:
    """唯一仍硬拒（DENY）的分支：坏输入——路径字符串解析失败（含 NUL 字节会让
    Path(...)/resolve() 抛 ValueError）。不是权限问题，不该弹窗请求授权，直接拒。
    回归守卫：Option B 把其余分支全改 ASK 后，确保这条坏输入分支没被一并改掉。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access("bad\x00path.json", tool, cwd, user_id=_USER_ID)
    assert decision is Decision.DENY, f"{tool} 的无效路径应硬拒，却被判 {decision}"
    assert reason
    assert "无效" in reason


def test_read_lib_passes(policy: AgentAccessPolicy) -> None:
    """cwd 外的非 projects 路径允许读（用于 Agent 查 docs/lib 等参考资料）。"""
    cwd = _cwd(policy)
    decision, _ = policy.check_path_access(str(policy.project_root / "lib" / "foo.py"), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW


def test_write_cwd_external_asks(policy: AgentAccessPolicy) -> None:
    """cwd 外写改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(
        str(policy.project_root / "lib" / "foo.json"), "Write", cwd, user_id=_USER_ID
    )
    assert decision is Decision.ASK
    assert "项目目录之外" in reason or "cwd" in reason or "项目" in reason


def test_write_cwd_internal_code_ext_asks(policy: AgentAccessPolicy) -> None:
    """代码扩展名写入改为弹窗请求授权（ASK）：用户可批准本次写入。"""
    cwd = _cwd(policy)
    for ext in (".py", ".js", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml"):
        decision, reason = policy.check_path_access(str(cwd / f"test{ext}"), "Write", cwd, user_id=_USER_ID)
        assert decision is Decision.ASK, f"扩展名 {ext} 应改为弹窗请求授权"
        assert "代码" in reason or "扩展名" in reason


def test_write_cwd_internal_data_ext_allowed(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    for ext in (".json", ".md", ".txt", ".html", ".csv"):
        decision, _ = policy.check_path_access(str(cwd / f"data{ext}"), "Write", cwd, user_id=_USER_ID)
        assert decision is Decision.ALLOW, f"扩展名 {ext} 应允许"


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["scripts/episode_1.json", "scripts/episode_10.json", "project.json"])
def test_write_protected_project_json_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """scripts/*.json 与 project.json 用 Write/Edit 直改改为弹窗请求授权（ASK），reason 仍指向 MCP 工具。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert (reason and "patch_episode_script" in reason) or "patch_project" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["edit_timelines/episode_1/tl-0000abcd.json", "edit_timelines/notes.md"])
def test_write_edit_timeline_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """剪辑时间线用 Write/Edit 直改改为弹窗请求授权（ASK），reason 仍指向工具。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert "create_timeline" in (reason or "")
    assert "edit_timeline" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative", [".arcreel/trash/20261025T000000Z-0000abcd/record.json", ".arcreel/trash/x/files/notes.txt"]
)
def test_write_trash_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """项目回收站只经回收站模块增删，Agent 直写会绕过「清空只开放给创作者」；项目记忆不受影响。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert ".arcreel/trash/" in (reason or "")
    assert policy.check_path_access(str(cwd / ".arcreel/memory/MEMORY.md"), tool, cwd, user_id=_USER_ID)[0]


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    [
        "storyboards/scene_E1S01.png",
        "videos/scene_E1S01.mp4",
        "characters/derivatives/Alice/young.png",
        "reference_videos/thumbnails/E1U1.jpg",
        "renders/episode_1/final.mp4",
        "style_reference.png",
        "style_reference.jpg",
        "style_reference.jpeg",
        "style_reference.webp",
        "STYLE_REFERENCE.PNG",
    ],
)
def test_write_media_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """媒体只经生成与编辑工具写入，Agent 直写或删除会绕过项目回收站；报错指向回收站删除工具。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert "delete_project_file" in (reason or "")


def test_protected_media_covers_every_public_media_location() -> None:
    """写保护表从媒体的唯一定义处枚举：公开端点可读的每个媒体目录与项目根目录的媒体文件，
    都在 Agent 写保护里。新增媒体位置只登记到公开端点时，Agent 能绕过项目回收站直接覆盖或删除它。"""
    from lib.project.data_validator import DataValidator
    from server.agent_runtime.agent_access_policy import AGENT_PROTECTED_MEDIA_DIRS, AGENT_PROTECTED_MEDIA_FILES
    from server.routers.files import PUBLIC_MEDIA_DIRS, is_public_media_path

    assert set(AGENT_PROTECTED_MEDIA_DIRS) >= PUBLIC_MEDIA_DIRS
    root_media = {name for name in DataValidator.ALLOWED_ROOT_ENTRIES if is_public_media_path((name,))}
    assert root_media
    assert root_media <= set(AGENT_PROTECTED_MEDIA_FILES)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["versions/versions.json", "versions/storyboards/scene_E1S01_v1_20261025.png"])
def test_write_version_history_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """版本历史只经版本管理维护，Agent 直写或删除会绕过项目回收站。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert "versions/" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative", ["source/novel.txt", "source/episode_1.txt", "source/snapshots/novel.txt", "source/new.md"]
)
def test_write_source_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """source/ 用 Write/Edit 直改改为弹窗请求授权（ASK），reason 仍指向上传与编辑源文的工具。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert "upload_source" in (reason or "")
    assert "edit_source_text" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    [
        "drafts/episode_1/script_plan_reference_units.json",
        "drafts/episode_12/script_plan_reference_units.json",
        "drafts/episode_1/SCRIPT_PLAN_REFERENCE_UNITS.JSON",
        "drafts/episode_1/script_plan_normalized_script.json",
        "drafts/episode_12/script_plan_normalized_script.json",
        "drafts/episode_1/SCRIPT_PLAN_NORMALIZED_SCRIPT.JSON",
        "drafts/episode_1/script_plan_segments.json",
        "drafts/episode_12/script_plan_segments.json",
        "drafts/episode_1/SCRIPT_PLAN_SEGMENTS.JSON",
    ],
)
def test_write_formal_script_plan_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """三条路线的正式 script_plan 用 Write/Edit 直改改为弹窗请求授权（ASK）：它另有几条持同一把 per-path 锁的写入路径
    （迁移 / Web 端保存 / 晋升），沙箱内的 Write/Edit 取不到锁，直改即丢失更新窗口。
    reason 要指向取回草稿的工具，否则 Agent 只知弹窗、不知改道哪里。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert reason
    assert "open_draft" in reason
    assert "promote_draft" in reason


def test_protected_script_plan_filenames_match_shared_constant() -> None:
    """写禁清单只认 lib.episode.episode_paths 那一份常量：判定表与文件名真相源分开声明时，
    改名或新增变体会只落到其中一处，留出「文件已换名、写禁还拦旧名」的静默旁路。"""
    from lib.episode.episode_paths import AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES

    assert (
        frozenset(
            AgentAccessPolicy._normalize_path_for_protected_compare(Path(name))
            for name in AGENT_PROTECTED_SCRIPT_PLAN_FILENAMES
        )
        == AgentAccessPolicy._PROTECTED_SCRIPT_PLAN_FILENAMES_NORM
    )


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    [
        "drafts/episode_1/script_plan_narration_segments.json",
        "drafts/script_plan_reference_units.json",
        "drafts/episode_1/sub/script_plan_reference_units.json",
    ],
)
def test_write_near_formal_script_plan_allowed(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """写禁不外溢到未注册的同目录邻居。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, f"{tool} {relative} 应允许，却被判 {decision}：{reason}"


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    [
        "drafts/episode_1/script_plan_reference_units.invalid.json",
        "drafts/episode_1/script_plan_normalized_script.invalid.json",
        "drafts/episode_1/script_plan_segments.invalid.json",
        "drafts/episode_1/prompt_authoring_reference_script.invalid.json",
    ],
)
def test_write_revisioned_draft_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert reason
    assert "patch_draft" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_scripts_dir_itself_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """`scripts/` 目录路径本身（不带 trailing sep）也该弹窗请求授权：defense-in-depth，
    不依赖 OS 兜底 Agent 把目录名当文件路径的 typo。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / "scripts"), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["scripts/episode_1.bak", "scripts/notes.md", "scripts/.tmp", "scripts/subdir/anything.txt"],
)
def test_write_protected_scripts_non_json_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """`scripts/` 下任意文件类型都该弹窗请求授权（不只 .json）：sandbox denyWrite 把整个 scripts/ 列入
    内核级 deny，hook 层须保持一致（Bash 子进程仍硬拒），避免 Agent 用 Write 污染剧本目录。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["PROJECT.JSON", "Project.Json", "scripts/EPISODE_1.JSON", "Scripts/episode_1.json"],
)
def test_write_protected_case_variants_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """大小写变体（PROJECT.JSON / Scripts/x.json）在 Windows NTFS / macOS APFS 默认卷
    上指向同一物理文件，Path 字符串比较 case-sensitive 会漏判——`_is_protected_project_json`
    用 casefold 比较后这类变体也应命中，改为弹窗请求授权，否则 Agent 可改大小写绕过收口。"""
    cwd = _cwd(policy)
    decision, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_project_json_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """`project.json` 本身被做成项目内 symlink（指向另一个项目内文件）时，仍须命中——
    防止"把入口换成 symlink"绕过 protected 区判定，改为弹窗请求授权。仅靠 resolve 后路径比较会失配。"""
    cwd = _cwd(policy)
    real = cwd / "other.json"
    real.write_text("{}", encoding="utf-8")
    link = cwd / "project.json"
    link.symlink_to(real)
    decision, reason = policy.check_path_access(str(link), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, "symlink 形态的 project.json 写入应改为弹窗请求授权"
    assert reason
    assert "patch_project" in reason or "patch_episode_script" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_scripts_dir_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """`scripts/` 整个目录被做成项目内 symlink 时，对其下 .json 的写入仍须命中（弹窗请求授权）。"""
    cwd = _cwd(policy)
    real_dir = cwd / "data"
    real_dir.mkdir()
    link_dir = cwd / "scripts"
    link_dir.symlink_to(real_dir)
    target = link_dir / "episode_1.json"
    decision, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, "symlink 形态的 scripts/ 下 .json 写入应改为弹窗请求授权"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_with_symlinked_project_cwd_asks(policy: AgentAccessPolicy, tmp_path: Path, tool: str) -> None:
    """project_cwd 本身是个 symlink 指向真实项目目录时(macOS /var↔/private/var、Linux
    symlinked 项目根),`_is_protected_project_json` 要把 base 也按 resolve 一次再拼接 protected
    路径,避免 resolved target 与 raw base 字符串不等 → bypass。"""
    # 真实项目目录在 tmp 根的另一处,通过 symlink 暴露
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = _projects_dir(policy) / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    # caller 把 symlinked cwd 传入,check_path_access 内 logical.resolve() 会展开 symlink,
    # 然后 _check_write_access 把 resolved target 与原始 link_cwd 比较——若不把 base 也
    # resolve,就会因为字符串不等漏判。
    decision, reason = policy.check_path_access(str(link_cwd / "project.json"), tool, link_cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, "symlinked project_cwd 下 project.json 写入应改为弹窗请求授权"
    assert reason
    assert "patch_project" in reason or "patch_episode_script" in reason

    decision, reason = policy.check_path_access(
        str(link_cwd / "scripts" / "episode_1.json"), tool, link_cwd, user_id=_USER_ID
    )
    assert decision is Decision.ASK, "symlinked project_cwd 下 scripts/*.json 写入应改为弹窗请求授权"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


def test_protected_json_predicate_normalizes_nfd_and_case() -> None:
    """NFC/NFD 与大小写混合形式都须命中：macOS HFS+ 按 NFD 存储文件名，resolve
    返回的 target 与 NFC 形式的 base 即使 casefold 后仍是不同字符串——受保护
    比对须先做 NFC 归一化，再做大小写不敏感比较。"""
    base_nfc = Path("/data/projects/café")  # café（NFC 单码位）
    target_nfd = Path("/data/projects/café/project.json")  # café（NFD 组合字符）
    assert AgentAccessPolicy._is_protected_project_json(target_nfd, [base_nfc])

    # 大小写变体 + NFD 叠加
    target_mixed = Path("/data/projects/CAFÉ/SCRIPTS/EPISODE_1.JSON")
    assert AgentAccessPolicy._is_protected_project_json(target_mixed, [base_nfc])

    # 反向：base 是 NFD（HFS+ 磁盘形式）、target 是 NFC（用户输入形式）
    base_nfd = Path("/data/projects/café")
    target_nfc = Path("/data/projects/café/scripts/episode_1.json")
    assert AgentAccessPolicy._is_protected_project_json(target_nfc, [base_nfd])

    # 归一化不引入 over-match：其他项目路径不受影响
    other = Path("/data/projects/cafe_other/project.json")
    assert not AgentAccessPolicy._is_protected_project_json(other, [base_nfc])


def test_normalize_path_for_protected_compare_strips_windows_extended_prefix() -> None:
    """Windows ``\\\\?\\`` 扩展长度前缀（resolve 在长路径/UNC 下返回）与常规形态
    须归一化为同一比较键，否则 bases 混入两种形式时 startswith 失配。
    helper 级单测；实机 Windows 端到端验证另行跟踪。"""
    norm = AgentAccessPolicy._normalize_path_for_protected_compare
    assert norm("\\\\?\\C:\\data\\projects\\demo") == norm("C:\\data\\projects\\demo")
    assert norm("\\\\?\\UNC\\server\\share\\proj") == norm("\\\\server\\share\\proj")
    # 常规路径不受影响
    assert norm("/data/projects/demo") == norm("/data/projects/demo")


def test_write_drafts_still_allowed(policy: AgentAccessPolicy) -> None:
    """合法的草稿写入不受影响（drafts/*.md、scripts 外的 .json）。"""
    cwd = _cwd(policy)
    for relative in ("drafts/episode_1/script_plan_segments.md", "config_data.json"):
        decision, _ = policy.check_path_access(str(cwd / relative), "Write", cwd, user_id=_USER_ID)
        assert decision is Decision.ALLOW, f"{relative} 应允许"


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        ".env.production",
    ],
)
@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_sensitive_file_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """敏感文件无论 Read 还是 Write 改为弹窗请求授权（ASK），且报错信息含"敏感文件"。"""
    cwd = _cwd(policy)
    # 文件实际存在与否不影响判断（resolve() 对不存在路径仍返回绝对路径）
    target = policy.project_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    decision, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} {relative} 应改为弹窗请求授权"
    assert reason
    assert "敏感文件" in reason


@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_agent_profile_settings_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """敏感判断对准构造时传入的 agent_profile_root，命中后改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    target = policy.agent_profile_root / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    decision, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} agent_profile settings.json 应改为弹窗请求授权"
    assert reason
    assert "敏感文件" in reason


def test_read_host_file_outside_project_root_asks(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """project_root 外的 host 文件（~/.ssh、/etc 等）改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    # tmp_path 在 policy.project_root 之外（project_root = tmp_path / "repo"）
    outside = tmp_path / "host_fake_ssh"
    outside.mkdir()
    (outside / "id_rsa").write_text("secret", encoding="utf-8")
    for tool in ("Read", "Glob", "Grep"):
        decision, reason = policy.check_path_access(str(outside / "id_rsa"), tool, cwd, user_id=_USER_ID)
        assert decision is Decision.ASK, f"{tool} 读 project_root 外的 host 文件应改为弹窗请求授权"
        assert reason
        assert "host 路径" in reason or "项目根外" in reason


def test_sensitive_glob_pattern_does_not_overmatch(policy: AgentAccessPolicy) -> None:
    """`.env.*` 不能误伤 `.environment` 这种命名的合法目录/文件。"""
    cwd = _cwd(policy)
    legal = policy.project_root / ".environment"
    legal.parent.mkdir(parents=True, exist_ok=True)
    decision, _ = policy.check_path_access(str(legal), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, ".environment 是合法文件，不应被 `.env.*` glob 误伤"


# ============================================================
# 数据根默认拒读：只放行当前项目与当前用户的记忆
# ============================================================


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
def test_read_of_data_root_outside_current_project_and_memory_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """数据根位于仓库根内（开发默认布局）时，数据根里的条目不因「仓库根内参考资料」被放行：
    布局登记的系统条目、旧布局残留、布局未登记的条目、根下与项目目录下的直放文件改为弹窗请求授权（ASK）。"""
    layout = DataRootLayout(policy.data_root)
    assert policy.data_root.is_relative_to(policy.project_root)
    entries = [entry for entry in layout.top_level_entries if entry != layout.projects_dir]
    entries += [
        policy.data_root / name
        for name in (".arcreel.db", ".arcreel", ".system_config.json", "stray.txt", "future_dir")
    ]
    entries += [layout.projects_dir, layout.projects_dir / "stray.txt"]
    for entry in entries:
        for target in (entry, entry / "nested.json"):
            decision, reason = policy.check_path_access(str(target), tool, _cwd(policy), user_id=_USER_ID)
            assert decision is Decision.ASK, f"{tool} {target} 应改为弹窗请求授权"
            assert reason


# ============================================================
# 内核 settings 编译投影（原 sandbox settings 测试断言搬家）
# ============================================================


def test_build_sandbox_settings_disabled_returns_only_enabled_false(tmp_path: Path) -> None:
    """sandbox_enabled=False（Windows 回退）时只返回 {"enabled": False}。"""
    policy = _make_policy(tmp_path, sandbox_enabled=False)
    cwd = _projects_dir(policy) / "demo"
    assert policy.build_sandbox_settings(cwd) == {"enabled": False}


def test_build_sandbox_settings_enabled_returns_full_config(tmp_path: Path) -> None:
    """sandbox_enabled=True 时出站与 loopback 均显式放行，文件围栏保持完整。"""
    policy = _make_policy(tmp_path, sandbox_enabled=True)
    cwd = _projects_dir(policy) / "demo"
    settings = policy.build_sandbox_settings(cwd)
    assert settings["enabled"] is True
    assert settings["autoAllowBashIfSandboxed"] is True
    assert settings["allowUnsandboxedCommands"] is False
    # 省略 network 键等于零预放行（出站全断），全放行必须显式写 ``*``；
    # loopback 另由 allowLocalBinding 控制，allowedDomains 覆盖不到。
    assert settings["network"] == {"allowedDomains": ["*"], "allowLocalBinding": True}
    assert "denyRead" in settings["filesystem"]
    assert str(cwd / "project.json") in settings["filesystem"]["denyWrite"]


def test_build_sandbox_settings_in_docker_enables_weaker_nested(tmp_path: Path) -> None:
    """in_docker 透传到 enableWeakerNestedSandbox；非 Docker 默认 False。"""
    cwd = _projects_dir(_make_policy(tmp_path)) / "demo"
    assert _make_policy(tmp_path).build_sandbox_settings(cwd)["enableWeakerNestedSandbox"] is False
    assert _make_policy(tmp_path, in_docker=True).build_sandbox_settings(cwd)["enableWeakerNestedSandbox"] is True


def test_build_sandbox_settings_denies_write_to_project_json(policy: AgentAccessPolicy) -> None:
    """sandbox 启用时 denyWrite 覆盖 scripts/、project.json、edit_timelines/、drafts/、source/、.arcreel/trash/、媒体目录与 versions/（Bash 子进程内核级封堵）。"""
    cwd = _cwd(policy)
    settings = policy.build_sandbox_settings(cwd)
    deny_write = settings["filesystem"]["denyWrite"]
    assert str(cwd / "scripts") in deny_write
    assert str(cwd / "project.json") in deny_write
    assert str(cwd / "drafts") in deny_write
    assert str(cwd / "edit_timelines") in deny_write
    assert str(cwd / "source") in deny_write
    assert str(cwd / ".arcreel/trash") in deny_write
    assert str(cwd / "storyboards") in deny_write
    assert str(cwd / "characters") in deny_write
    assert str(cwd / "versions") in deny_write
    for ext in ("png", "jpg", "jpeg", "webp"):
        assert str(cwd / f"style_reference.{ext}") in deny_write


def test_build_sandbox_settings_denies_drafts_dir_not_per_episode_files(policy: AgentAccessPolicy) -> None:
    """``drafts/`` 按整目录 deny，不逐文件枚举——清单在会话装配期一次性构造，而集是运行时
    增删的：「同集会话内先拆分出第 N 集、再改它」这条主流程上，逐文件枚举必然落空。
    与 hook 层刻意不对称（hook 只拒正式 script_plan，草稿留给内置 Edit）。"""
    cwd = _cwd(policy)
    deny_write = policy.build_sandbox_settings(cwd)["filesystem"]["denyWrite"]
    assert str(cwd / "drafts") in deny_write
    assert not any("episode_" in p for p in deny_write)


def test_build_sandbox_settings_deny_write_includes_resolved_paths(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """project_cwd 是 symlink 入口时（macOS /var↔/private/var、Linux symlinked
    项目根），denyWrite 须同时枚举 raw 与 resolved 两种形式——sandbox 实现若按
    字符串路径比对，仅注册 raw 形式会在 Bash 子进程经 symlink 解析后写 resolved
    路径时失配。与 _check_write_access 的 bases 同口径。"""
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = _projects_dir(policy) / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    settings = policy.build_sandbox_settings(link_cwd)
    deny_write = settings["filesystem"]["denyWrite"]
    resolved_cwd = link_cwd.resolve()
    assert resolved_cwd != link_cwd
    # raw 与 resolved 两种形式都注册
    assert str(link_cwd / "scripts") in deny_write
    assert str(link_cwd / "project.json") in deny_write
    assert str(link_cwd / "drafts") in deny_write
    assert str(resolved_cwd / "scripts") in deny_write
    assert str(resolved_cwd / "project.json") in deny_write
    assert str(resolved_cwd / "drafts") in deny_write
    # raw == resolved 的常规路径不重复注册
    assert len(deny_write) == len(set(deny_write))


def test_sandbox_denies_read_of_existing_sensitive_files_outside_data_root(tmp_path: Path) -> None:
    """数据根外的敏感文件按当前实际存在的逐个进 denyRead，不存在的跳过。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("X=1", encoding="utf-8")
    (root / ".env.local").write_text("Y=2", encoding="utf-8")
    profile_dir = tmp_path / "agent_runtime_profile"
    (profile_dir / ".claude").mkdir(parents=True)
    (profile_dir / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    policy = _make_policy(tmp_path)
    deny_read = policy.build_sandbox_settings(_projects_dir(policy) / "demo")["filesystem"]["denyRead"]

    assert str(root.resolve() / ".env") in deny_read
    assert str(root.resolve() / ".env.local") in deny_read
    assert str(profile_dir.resolve() / ".claude" / "settings.json") in deny_read
    assert all(".env.production" not in p for p in deny_read)


def test_sandbox_denies_read_of_every_data_root_entry_but_projects(policy: AgentAccessPolicy) -> None:
    """Bash 不经读 hook：数据根里项目目录以外的顶层条目——含旧布局残留与布局未登记的条目——
    都在 denyRead 里；项目目录不在，Bash 照常读写当前项目。"""
    root = policy.data_root
    (root / "arcreel.db").write_bytes(b"db")
    (root / ".arcreel.db-wal").write_bytes(b"wal")
    (root / ".arcreel" / "users").mkdir(parents=True)
    (root / ".system_config.json").write_text("{}", encoding="utf-8")
    (root / "future_dir").mkdir()

    deny_read = policy.build_sandbox_settings(_cwd(policy))["filesystem"]["denyRead"]

    for name in ("arcreel.db", ".arcreel.db-wal", ".arcreel", ".system_config.json", "future_dir"):
        assert str(root / name) in deny_read
    for system_dir in DataRootLayout(root).system_dirs:
        assert str(system_dir) in deny_read
    assert not any(_cwd(policy).is_relative_to(path) for path in deny_read)


def test_sandbox_denies_system_dirs_created_after_session_start(policy: AgentAccessPolicy) -> None:
    """CLI 跳过不存在的 deny 路径，而围栏只在会话启动时编译一次：会话启动时还不存在的
    系统目录（全新安装上别的用户的记忆、首次上传的凭证）会先被建出来，deny 从一开始就生效。"""
    system_dirs = DataRootLayout(policy.data_root).system_dirs
    assert not any(system_dir.exists() for system_dir in system_dirs)

    deny_read = policy.build_sandbox_settings(_cwd(policy))["filesystem"]["denyRead"]

    for system_dir in system_dirs:
        assert system_dir.is_dir(), f"{system_dir} 应在会话启动时建出"
        assert str(system_dir) in deny_read


def test_filter_allowed_tools_strips_bash_family_when_sandbox_disabled(tmp_path: Path) -> None:
    """sandbox 关闭时剥离 Bash/BashOutput/KillBash（落到 can_use_tool 白名单），启用时保留。"""
    base = ["Skill", "Bash", "BashOutput", "KillBash", "Read"]
    enabled = _make_policy(tmp_path, sandbox_enabled=True)
    assert enabled.filter_allowed_tools(base) == base
    disabled = _make_policy(tmp_path, sandbox_enabled=False)
    assert disabled.filter_allowed_tools(base) == ["Skill", "Read"]


# ============================================================
# Bash 密钥剥离（env scrub）纯变换
# ============================================================


def test_env_scrub_collects_pattern_matched_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """unset 清单除了固定名单还要动态命中 *_API_KEY / *_AUTH_TOKEN 等模式。"""
    monkeypatch.setenv("GEMINI_CLI_IDE_AUTH_TOKEN", "abc")
    monkeypatch.setenv("RANDOM_VENDOR_API_KEY", "def")
    monkeypatch.setenv("PATH", "/usr/bin")  # 不应命中

    AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
    AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()
    try:
        keys = AgentAccessPolicy._collect_env_keys_to_scrub()
        assert "GEMINI_CLI_IDE_AUTH_TOKEN" in keys
        assert "RANDOM_VENDOR_API_KEY" in keys
        assert "PATH" not in keys
        # 固定清单
        assert "ANTHROPIC_API_KEY" in keys
        assert "ARK_API_KEY" in keys
    finally:
        AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
        AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()


def test_wrap_bash_command_unsets_provider_keys(tmp_path: Path) -> None:
    """POSIX（sandbox 启用）：command 包装成 ``env -u ANTHROPIC_* sh -c '<orig>'``。"""
    from lib.config.env_keys import ANTHROPIC_ENV_KEYS

    policy = _make_policy(tmp_path)
    wrapped = policy.wrap_bash_command_for_env_scrub("env | grep ANTHROPIC")

    assert wrapped is not None
    # 每个 ANTHROPIC_* key 都被 unset
    for key in ANTHROPIC_ENV_KEYS:
        assert f"-u {key}" in wrapped
    # 原命令被 shlex.quote 包到 sh -c 内
    assert "sh -c " in wrapped
    assert "'env | grep ANTHROPIC'" in wrapped


def test_wrap_bash_command_preserves_short_lived_arcreel_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """skill 脚本可从 Bash 子进程读取专用短期 JWT，其余 secret-like env 仍剥离。"""
    monkeypatch.setenv("ARCREEL_API_TOKEN", "session-jwt")
    monkeypatch.setenv("RANDOM_VENDOR_AUTH_TOKEN", "provider-secret")
    AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
    AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()
    try:
        wrapped = _make_policy(tmp_path).wrap_bash_command_for_env_scrub("python skill.py validate x.json")
        assert wrapped is not None
        assert "-u ARCREEL_API_TOKEN" not in wrapped
        assert "-u RANDOM_VENDOR_AUTH_TOKEN" in wrapped
    finally:
        AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
        AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()


def test_wrap_bash_command_skips_when_sandbox_disabled(tmp_path: Path) -> None:
    """Windows 回退：``env -u``/``sh -c`` 是 POSIX 机制，原生 Windows 不可执行；
    且包装后的命令以 ``env -u`` 开头，会让白名单永远匹配不上——返回 None 表示
    不包装，原始命令落到 can_use_tool 做白名单匹配。"""
    policy = _make_policy(tmp_path, sandbox_enabled=False)
    assert policy.wrap_bash_command_for_env_scrub("python .claude/skills/x/scripts/run.py") is None


def test_wrap_bash_command_handles_single_quotes(tmp_path: Path) -> None:
    """命令含单引号时不能破坏 shell 引号闭合。"""
    policy = _make_policy(tmp_path)
    wrapped = policy.wrap_bash_command_for_env_scrub("echo 'hello world'")
    assert wrapped is not None
    # shlex.quote 把 'hello world' 转义为 'echo '"'"'hello world'"'"''
    assert wrapped.endswith("'\"'\"'hello world'\"'\"''")


def test_wrap_bash_command_passthrough_when_no_command(tmp_path: Path) -> None:
    """空 command 时不做包装。"""
    policy = _make_policy(tmp_path)
    assert policy.wrap_bash_command_for_env_scrub(None) is None
    assert policy.wrap_bash_command_for_env_scrub("   ") is None


# ============================================================
# 受保护写路径规则表：hook 与 sandbox denyWrite 同表投影
# ============================================================


def test_protected_write_rules_project_new_rule_in_both_layers(
    policy: AgentAccessPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新增一类受保护路径只需在 PROTECTED_WRITE_RULES 加一行：hook 拒绝与 sandbox denyWrite
    都从同一张表投影、随之同步生效——分散声明时漏掉任一处都会留出单层旁路。"""
    from server.agent_runtime.agent_access_policy import ProtectedWriteRule

    def _matches_meta_lock(target: Path, bases: list[Path]) -> bool:
        return any(str(target) == str(base / "meta.lock") for base in bases)

    synthetic = ProtectedWriteRule(
        name="meta_lock",
        matches=_matches_meta_lock,
        deny_message="访问被拒绝：meta.lock 不可直改（合成规则）",
        sandbox_subpaths=("meta.lock",),
    )
    monkeypatch.setattr(
        AgentAccessPolicy, "PROTECTED_WRITE_RULES", (*AgentAccessPolicy.PROTECTED_WRITE_RULES, synthetic)
    )

    cwd = _cwd(policy)
    # hook 层：新规则立即生效（受保护写改为弹窗请求授权 ASK）
    decision, reason = policy.check_path_access(str(cwd / "meta.lock"), "Write", cwd, user_id=_USER_ID)
    assert decision is Decision.ASK
    assert reason == synthetic.deny_message
    # sandbox 层：denyWrite 投影同表同步
    deny_write = policy.build_sandbox_settings(cwd)["filesystem"]["denyWrite"]
    assert str(cwd / "meta.lock") in deny_write
    # 既有规则不受影响
    assert str(cwd / "project.json") in deny_write
    assert str(cwd / "scripts") in deny_write
    assert str(cwd / "drafts") in deny_write


# ============================================================
# 两级记忆目录围栏（ADR 0074）
# ============================================================


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_user_memory_dir_allowed_for_all_path_tools(policy: AgentAccessPolicy, tool: str) -> None:
    """用户记忆目录在 cwd 外、且落在数据根下——五个工具都要显式放行，
    否则读被跨项目隔离拒、写被 cwd 外拒。"""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).user_memory_dir(_USER_ID) / "MEMORY.md"
    decision, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, f"{tool} 访问用户记忆应放行：{reason}"


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_user_memory_subdirectory_allowed(policy: AgentAccessPolicy, tool: str) -> None:
    """放行按子树而非单文件：记忆索引之外的笔记文件同样可读写。"""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).user_memory_dir(_USER_ID) / "notes" / "style.md"
    decision, _ = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_other_users_memory_dir_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """放行只覆盖当前 user_id 的子树；别人的记忆目录（在数据根内）改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).user_memory_dir("someone-else") / "MEMORY.md"
    decision, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK, f"{tool} 访问他人记忆应改为弹窗请求授权"
    assert reason


@pytest.mark.parametrize("tool", ["Read", "Write"])
def test_users_namespace_root_outside_memory_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """放行的是 ``<user_id>/memory/``，同用户目录下的其它子树（在数据根内）改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).users_dir / _USER_ID / "secrets" / "x.md"
    decision, _ = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK


@pytest.mark.parametrize("tool", ["Read", "Write"])
def test_data_root_other_paths_ask_with_memory_allowance(policy: AgentAccessPolicy, tool: str) -> None:
    """记忆放行不外溢到数据根下的其它路径：别的项目目录（在数据根内）改为弹窗请求授权（ASK）。"""
    cwd = _cwd(policy)
    decision, _ = policy.check_path_access(str(_projects_dir(policy) / "other" / "x.json"), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ASK


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_project_memory_dir_allowed(policy: AgentAccessPolicy, tool: str) -> None:
    """项目记忆是 ``<cwd>/.arcreel/memory/``，由 cwd 围栏放行，五个工具都可读写。"""
    cwd = _cwd(policy)
    target = project_memory_dir(cwd) / "MEMORY.md"
    decision, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, f"{tool} 访问项目记忆应放行：{reason}"


def test_memory_dirs_still_ask_for_code_extensions(policy: AgentAccessPolicy) -> None:
    """记忆装的是笔记：记忆目录内的代码扩展名写入也改为弹窗请求授权（ASK），用户可批准本次写入。"""
    cwd = _cwd(policy)
    for target in (
        project_memory_dir(cwd) / "hack.sh",
        DataRootLayout(policy.data_root).user_memory_dir(_USER_ID) / "hack.sh",
    ):
        decision, reason = policy.check_path_access(str(target), "Write", cwd, user_id=_USER_ID)
        assert decision is Decision.ASK, f"{target} 应被代码扩展名规则改为弹窗请求授权"
        assert "代码" in reason


@pytest.mark.parametrize("bad_user_id", ["", "..", "../other", "a/b", "\x00"])
def test_invalid_user_id_does_not_widen_memory(policy: AgentAccessPolicy, bad_user_id: str) -> None:
    """user_id 不是单个路径段时 fail-closed：不把记忆路径直接放行（ALLOW），逃出数据根的路径不被当作用户记忆。

    非当前用户记忆的 cwd 外写走弹窗请求授权（ASK）而非直接放行——仍需用户点批准，不构成「无声放宽」。"""
    cwd = _cwd(policy)
    escaped = DataRootLayout(policy.data_root).user_memory_dir("victim") / "MEMORY.md"
    decision, _ = policy.check_path_access(str(escaped), "Write", cwd, user_id=bad_user_id)
    assert decision is not Decision.ALLOW


@pytest.mark.parametrize(
    "in_docker",
    [pytest.param(False, id="macos-seatbelt-or-linux-bwrap"), pytest.param(True, id="docker-linux-bwrap")],
)
def test_build_sandbox_settings_gives_bash_no_access_to_user_memory(tmp_path: Path, in_docker: bool) -> None:
    """内核沙箱层不给用户记忆目录开任何口子，Bash 读写都被拒，两个平台的编译结果相同。

    bwrap 把 ``allowWrite`` 编成可写绑定挂载，会盖掉 ``users/`` 的 ``denyRead``；Seatbelt
    则读拒写放。内置 Read/Write/Edit/Glob/Grep 不经内核沙箱，由 hook 层放行记忆目录。
    """
    policy = _make_policy(tmp_path, in_docker=in_docker)
    cwd = _cwd(policy)
    layout = DataRootLayout(policy.data_root)
    filesystem = policy.build_sandbox_settings(cwd)["filesystem"]

    assert "allowWrite" not in filesystem
    assert str(layout.users_dir) in filesystem["denyRead"]
    # 内置工具照常读写用户记忆
    memory_file = layout.user_memory_dir(_USER_ID) / "MEMORY.md"
    for tool in ("Read", "Write", "Edit", "Glob", "Grep"):
        allowed, reason = policy.check_path_access(str(memory_file), tool, cwd, user_id=_USER_ID)
        assert allowed, f"{tool} 访问用户记忆应放行：{reason}"


# ============================================================
# 数据根默认拒读：大小写变体与包含数据根的搜索根
# ============================================================


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
@pytest.mark.parametrize(
    "relative",
    ["PROJECTS/vertex_keys/key.json", "Projects/users/bob/memory/a.md", "PROJECTS/projects/other/project.json"],
)
def test_read_of_data_root_case_variant_asks(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """大小写不敏感卷上 ``<仓库根>/PROJECTS`` 就是数据根：大小写变体不因「仓库根内参考资料」被放行，改为弹窗请求授权（ASK）。"""
    target = policy.project_root / relative
    decision, reason = policy.check_path_access(str(target), tool, _cwd(policy), user_id=_USER_ID)
    assert decision is Decision.ASK
    assert reason


@pytest.mark.parametrize("tool", ["Glob", "Grep"])
def test_search_rooted_above_data_root_asks(policy: AgentAccessPolicy, tool: str) -> None:
    """数据根位于仓库根内时，以仓库根为搜索根会递归扫进数据根，改为弹窗请求授权（ASK）；仓库内其他参考资料目录照常可搜。"""
    for search_root in (policy.project_root, policy.project_root / "."):
        decision, reason = policy.check_path_access(str(search_root), tool, _cwd(policy), user_id=_USER_ID)
        assert decision is Decision.ASK
        assert reason
    decision, _ = policy.check_path_access(str(policy.project_root / "lib"), tool, _cwd(policy), user_id=_USER_ID)
    assert decision is Decision.ALLOW


# ============================================================
# resume + store-backed 物化的 SDK tool-results 读放行
#
# store-backed resume/continue 时 SDK 自建临时 CLAUDE_CONFIG_DIR
# （``<tmp>/claude-resume-<随机>/``），tool-results 落到
# ``.../claude-resume-*/projects/<project_key>/<uuid-session>/tool-results/``。
# 该临时基准不在 ``~/.claude/projects`` 下，须按 SDK 临时树结构精确放行当前会话、
# 当前项目的 tool-results，同时对伪造/跨项目/host 路径仍 fail-closed。
# 用例经 ``resume_tmp_dir`` 字段注入可控的 tmp 根（``dataclasses.replace``），避免依赖机器
# $TMPDIR，也不 patch 生产私有 cached_property。
# ============================================================

#: 合法 session 段形态（SDK 用 UUID 命名 resume 会话目录）。
_RESUME_SESSION_UUID = "0123abcd-4567-89ab-cdef-0123456789ab"


def _resume_base(tmp_path: Path) -> Path:
    """造一个 SDK 风格的 resume 临时根 ``<tmp>/claude-resume-xxxx``（真实存在）。"""
    base = tmp_path / "claude-resume-xxxx"
    base.mkdir()
    return base


def _with_resume_tmp_dir(policy: AgentAccessPolicy, base: Path) -> AgentAccessPolicy:
    """把 policy 的 resume 临时基准经 ``resume_tmp_dir`` 字段注入为 ``base`` 的父目录，
    使派生前缀 ``<base.parent>/claude-resume-``（raw + resolved）对测试造出的
    ``claude-resume-`` 目录成立，不依赖机器真实 $TMPDIR、也不 patch 私有 cached_property。
    返回注入后的新 policy，调用方须改用它。"""
    return replace(policy, resume_tmp_dir=base.parent)


def _make_resume_tool_results(base: Path, encoded: str, session: str = _RESUME_SESSION_UUID) -> Path:
    """在 resume 临时基准下造 ``projects/<encoded>/<session>/tool-results/f.txt`` 并返回该文件。"""
    tool_results_dir = base / "projects" / encoded / session / "tool-results"
    tool_results_dir.mkdir(parents=True)
    result_file = tool_results_dir / "byvf5dyv5.txt"
    result_file.write_text("tool output", encoding="utf-8")
    return result_file


def test_read_resume_tool_results_allowed(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """正例（本次修复）：resume 临时基准 + 当前项目 encoded + 含 tool-results 段 → Read 放行。"""
    from lib.agent.agent_session_store import make_project_key

    cwd = _cwd(policy)
    base = _resume_base(tmp_path)
    policy = _with_resume_tmp_dir(policy, base)
    result_file = _make_resume_tool_results(base, make_project_key(cwd))

    decision, reason = policy.check_path_access(str(result_file), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, f"resume tool-results 应放行：{reason}"


def test_read_non_resume_tool_results_still_allowed(
    policy: AgentAccessPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：非 resume（``~/.claude/projects``）下 tool-results 仍放行——换用 make_project_key
    编码后 ``claude_projects_dir`` 例外仍命中。"""
    from lib.agent.agent_session_store import make_project_key

    cwd = _cwd(policy)
    claude_projects = tmp_path / "claude_home" / "projects"
    claude_projects.mkdir(parents=True)
    policy = replace(policy, claude_projects_dir=claude_projects)
    encoded = make_project_key(cwd)
    tool_results_dir = claude_projects / encoded / "abc-session" / "tool-results"
    tool_results_dir.mkdir(parents=True)
    result_file = tool_results_dir / "r.txt"
    result_file.write_text("x", encoding="utf-8")

    decision, reason = policy.check_path_access(str(result_file), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, f"非 resume tool-results 应放行：{reason}"


def test_read_resume_tool_results_other_project_key_not_allowed(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """负例：resume 临时基准但 encoded 段是别的项目 key → 不自动放行（锁定当前项目）；
    落到 host tmp 路径分支，改为弹窗请求授权（ASK）而非直接 ALLOW。"""
    from lib.agent.agent_session_store import make_project_key

    cwd = _cwd(policy)
    base = _resume_base(tmp_path)
    policy = _with_resume_tmp_dir(policy, base)
    other_cwd = _projects_dir(policy) / "other"
    result_file = _make_resume_tool_results(base, make_project_key(other_cwd))

    decision, reason = policy.check_path_access(str(result_file), "Read", cwd, user_id=_USER_ID)
    assert decision is not Decision.ALLOW, "别的项目 key 的 resume tool-results 不应自动放行"
    assert reason


def test_read_fake_tmp_tool_results_not_allowed(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """负例：$TMPDIR 下伪造的、非 claude-resume- 前缀的含 tool-results 路径 → 仍拒
    （前缀收窄为 claude-resume-，普通 tmp 目录与泛 claude- 任务目录都不匹配）。"""
    from lib.agent.agent_session_store import make_project_key

    cwd = _cwd(policy)
    base = _resume_base(tmp_path)
    policy = _with_resume_tmp_dir(policy, base)
    encoded = make_project_key(cwd)
    # 同一父目录下但不是 claude-resume- 前缀：伪造的 evil 目录，结构照抄也应拒
    evil = tmp_path / "evil-xxxx" / "projects" / encoded / _RESUME_SESSION_UUID / "tool-results"
    evil.mkdir(parents=True)
    evil_file = evil / "x.txt"
    evil_file.write_text("x", encoding="utf-8")

    decision, reason = policy.check_path_access(str(evil_file), "Read", cwd, user_id=_USER_ID)
    assert decision is not Decision.ALLOW, "非 claude-resume- 前缀的伪造 tool-results 不应自动放行"
    assert reason


def test_read_resume_tool_results_bad_session_segment_not_allowed(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """负例补强：claude-resume- 前缀 + 当前项目 encoded，但 session 段不是 UUID → 仍拒
    （防止在合法前缀下塞非会话目录读到意外文件）。"""
    from lib.agent.agent_session_store import make_project_key

    cwd = _cwd(policy)
    base = _resume_base(tmp_path)
    policy = _with_resume_tmp_dir(policy, base)
    result_file = _make_resume_tool_results(base, make_project_key(cwd), session="not-a-uuid")

    decision, reason = policy.check_path_access(str(result_file), "Read", cwd, user_id=_USER_ID)
    assert decision is not Decision.ALLOW, "session 段非 UUID 的 resume tool-results 不应自动放行"
    assert reason


def test_read_host_and_other_memory_with_resume_exception_not_allowed(
    policy: AgentAccessPolicy, tmp_path: Path
) -> None:
    """负例：~/.ssh、/etc、他人记忆目录 → 不自动放行（resume 例外不放宽这些分支），改走弹窗请求授权（ASK）。"""
    base = _resume_base(tmp_path)
    policy = _with_resume_tmp_dir(policy, base)
    cwd = _cwd(policy)

    outside = tmp_path / "host_fake_ssh"
    outside.mkdir()
    (outside / "id_rsa").write_text("secret", encoding="utf-8")
    decision, reason = policy.check_path_access(str(outside / "id_rsa"), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ASK
    assert reason

    other_memory = DataRootLayout(policy.data_root).user_memory_dir("someone-else") / "MEMORY.md"
    decision, reason = policy.check_path_access(str(other_memory), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ASK
    assert reason


def test_read_resume_tool_results_underscore_space_project_path(tmp_path: Path) -> None:
    """编码口径：含下划线/空格的项目路径，make_project_key 口径下 resume tool-results 仍能正确放行
    （换用 make_project_key 后 encoded 段与 SDK 实际写入目录逐字节一致，不再因 replace 两字符而漂移）。"""
    from lib.agent.agent_session_store import make_project_key

    policy = _make_policy(tmp_path)
    cwd = _projects_dir(policy) / "my_proj with space"
    cwd.mkdir(parents=True)
    base = _resume_base(tmp_path)
    policy = _with_resume_tmp_dir(policy, base)
    result_file = _make_resume_tool_results(base, make_project_key(cwd))

    decision, reason = policy.check_path_access(str(result_file), "Read", cwd, user_id=_USER_ID)
    assert decision is Decision.ALLOW, f"含下划线/空格路径的 resume tool-results 应放行：{reason}"
