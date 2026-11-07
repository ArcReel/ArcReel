"""AgentAccessPolicy pure-rule tests: constructor arguments and allow/deny assertions without environment/private-method monkeypatches.

Four path rules: deny sensitive files outside the data root, default-deny data-root reads, deny writes outside cwd, deny code extensions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lib.agent.agent_memory_paths import project_memory_dir
from lib.infra.data_root_layout import DataRootLayout
from server.agent_runtime.agent_access_policy import AgentAccessPolicy

#: Current user ID supplied per call (production obtains it from SessionManager CurrentUser context).
_USER_ID = "default"


def _make_policy(tmp_path: Path, **overrides: object) -> AgentAccessPolicy:
    """Pure policy construction from temporary roots; repository layout matches the old SessionManager fixture."""
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
# Construction purity: fake roots suffice; no SDK imports.
# ============================================================


def test_pure_construction_with_fake_roots() -> None:
    """Fake nonexistent roots plus sandbox_enabled suffice for pure construction and working decisions."""
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
    allowed, _ = policy.check_path_access(str(cwd / "data.json"), "Read", cwd, user_id=_USER_ID)
    assert allowed
    allowed, reason = policy.check_path_access(str(fake / "repo" / ".env"), "Read", cwd, user_id=_USER_ID)
    assert not allowed
    assert reason
    assert "敏感文件" in reason


def test_policy_module_does_not_import_sdk_types() -> None:
    """Rule source imports no SDK types; SDK wrapper permission-result types and hook signatures stay in the adapter."""
    module = sys.modules[AgentAccessPolicy.__module__]
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in source


# ============================================================
# Path read/write decisions (assertions moved from test_path_isolation_hook.py).
# ============================================================


def test_read_cwd_internal_passes(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    allowed, _ = policy.check_path_access(str(cwd / "data.json"), "Read", cwd, user_id=_USER_ID)
    assert allowed


def test_read_other_project_denied(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(
        str(_projects_dir(policy) / "other" / "x.json"), "Read", cwd, user_id=_USER_ID
    )
    assert not allowed
    assert "跨项目" in reason or "项目" in reason


def test_read_lib_passes(policy: AgentAccessPolicy) -> None:
    """Allow reading cwd-external non-projects paths for Agent reference material such as docs/lib."""
    cwd = _cwd(policy)
    allowed, _ = policy.check_path_access(str(policy.project_root / "lib" / "foo.py"), "Read", cwd, user_id=_USER_ID)
    assert allowed


def test_write_cwd_external_denied(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(
        str(policy.project_root / "lib" / "foo.json"), "Write", cwd, user_id=_USER_ID
    )
    assert not allowed
    assert "项目目录之外" in reason or "cwd" in reason or "项目" in reason


def test_write_cwd_internal_code_ext_denied(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    for ext in (".py", ".js", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml"):
        allowed, reason = policy.check_path_access(str(cwd / f"test{ext}"), "Write", cwd, user_id=_USER_ID)
        assert not allowed, f"扩展名 {ext} 应被拒"
        assert "代码" in reason or "扩展名" in reason


def test_write_cwd_internal_data_ext_allowed(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    for ext in (".json", ".md", ".txt", ".html", ".csv"):
        allowed, _ = policy.check_path_access(str(cwd / f"data{ext}"), "Write", cwd, user_id=_USER_ID)
        assert allowed, f"扩展名 {ext} 应允许"


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["scripts/episode_1.json", "scripts/episode_10.json", "project.json"])
def test_write_protected_project_json_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Write/Edit cannot directly change scripts/*.json or project.json; errors direct callers to MCP tools."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert (reason and "patch_episode_script" in reason) or "patch_project" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["edit_timelines/episode_1/tl-0000abcd.json", "edit_timelines/notes.md"])
def test_write_edit_timeline_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Editing timelines can only be written through timeline tools; errors name those tools."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert "create_timeline" in (reason or "")
    assert "edit_timeline" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative", [".arcreel/trash/20261025T000000Z-0000abcd/record.json", ".arcreel/trash/x/files/notes.txt"]
)
def test_write_trash_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Only the trash module adds/removes project-trash entries; direct Agent writes would bypass creator-only emptying. Project memory is unaffected."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
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
    """Only generation/editing tools write media; direct Agent writes or deletes bypass project trash. Errors direct callers to the trash-deletion tool."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert "delete_project_file" in (reason or "")


def test_protected_media_covers_every_public_media_location() -> None:
    """Enumerate write protections from the sole media definitions: every publicly readable media directory and project-root media file is protected. Registering a new location only at public endpoints would let Agents overwrite/delete it outside trash."""
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
    """Only version management maintains version history; direct Agent writes/deletes bypass project trash."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert "versions/" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative", ["source/novel.txt", "source/episode_1.txt", "source/snapshots/novel.txt", "source/new.md"]
)
def test_write_source_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Only service commands write source/; errors direct callers to source upload/editing tools."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
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
def test_write_formal_script_plan_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Write/Edit cannot directly change formal script_plan in any route. Other writers (migration / Web saves / promotion) share a per-path lock unavailable to sandboxed tools; direct changes create lost-update windows. Errors must name draft-checkout tools so the Agent knows the alternative."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason
    assert "open_draft" in reason
    assert "promote_draft" in reason


def test_protected_script_plan_filenames_match_shared_constant() -> None:
    """Write-denial paths use only lib.episode.episode_paths constants. Separate filename and decision tables would let renames/new variants update only one and silently bypass protections."""
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
    """Write denials do not spill onto unregistered sibling files in the same directory."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert allowed, f"{tool} {relative} 应允许，却被拒：{reason}"


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
def test_write_revisioned_draft_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason
    assert "patch_draft" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_scripts_dir_itself_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """Reject the `scripts/` directory path itself without a trailing separator, as defense in depth rather than relying on OS handling of directory-as-file typos."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / "scripts"), tool, cwd, user_id=_USER_ID)
    assert not allowed
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["scripts/episode_1.bak", "scripts/notes.md", "scripts/.tmp", "scripts/subdir/anything.txt"],
)
def test_write_protected_scripts_non_json_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Reject all file types under `scripts/`, not only .json: sandbox denyWrite protects the entire directory, so hooks must match and prevent Write contamination."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["PROJECT.JSON", "Project.Json", "scripts/EPISODE_1.JSON", "Scripts/episode_1.json"],
)
def test_write_protected_case_variants_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Case variants (PROJECT.JSON / Scripts/x.json) identify the same physical files on default Windows NTFS / macOS APFS volumes. Case-sensitive Path comparison would miss them; `_is_protected_project_json` casefold must reject these bypasses."""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_project_json_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """Reject `project.json` even when it is an intra-project symlink to another file. Resolving only the target would miss the protected entry and allow a symlink substitution bypass."""
    cwd = _cwd(policy)
    real = cwd / "other.json"
    real.write_text("{}", encoding="utf-8")
    link = cwd / "project.json"
    link.symlink_to(real)
    allowed, reason = policy.check_path_access(str(link), tool, cwd, user_id=_USER_ID)
    assert not allowed, "symlink 形态的 project.json 写入应被拒"
    assert reason
    assert "patch_project" in reason or "patch_episode_script" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_scripts_dir_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """Reject writes to .json under `scripts/` even when the directory itself is an intra-project symlink."""
    cwd = _cwd(policy)
    real_dir = cwd / "data"
    real_dir.mkdir()
    link_dir = cwd / "scripts"
    link_dir.symlink_to(real_dir)
    target = link_dir / "episode_1.json"
    allowed, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert not allowed, "symlink 形态的 scripts/ 下 .json 写入应被拒"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_with_symlinked_project_cwd_denied(
    policy: AgentAccessPolicy, tmp_path: Path, tool: str
) -> None:
    """When project_cwd is a symlink (macOS /var↔/private/var or Linux project-root links), `_is_protected_project_json` also resolves base before joining protected paths, avoiding bypass from resolved-target/raw-base inequality."""
    # Real project directory lies elsewhere under tmp and is exposed through a symlink.
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = _projects_dir(policy) / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    # Caller supplies symlinked cwd; check_path_access expands it with logical.resolve(),
    # then _check_write_access compares resolved target against raw link_cwd. Resolve the base too,
    # or unequal path strings would miss the match.
    allowed, reason = policy.check_path_access(str(link_cwd / "project.json"), tool, link_cwd, user_id=_USER_ID)
    assert not allowed, "symlinked project_cwd 下 project.json 写入应被拒"
    assert reason
    assert "patch_project" in reason or "patch_episode_script" in reason

    allowed, reason = policy.check_path_access(
        str(link_cwd / "scripts" / "episode_1.json"), tool, link_cwd, user_id=_USER_ID
    )
    assert not allowed, "symlinked project_cwd 下 scripts/*.json 写入应被拒"
    assert reason
    assert "patch_episode_script" in reason or "patch_project" in reason


def test_protected_json_predicate_normalizes_nfd_and_case() -> None:
    """Match combined NFC/NFD and case variants. HFS+ stores filenames as NFD; resolved targets and NFC bases remain unequal after casefold, so protected comparisons normalize NFC before case-insensitive matching."""
    base_nfc = Path("/data/projects/café")  # café (NFC single code point)
    target_nfd = Path("/data/projects/café/project.json")  # café (NFD combining characters)
    assert AgentAccessPolicy._is_protected_project_json(target_nfd, [base_nfc])

    # Combined case variant + NFD
    target_mixed = Path("/data/projects/CAFÉ/SCRIPTS/EPISODE_1.JSON")
    assert AgentAccessPolicy._is_protected_project_json(target_mixed, [base_nfc])

    # Reverse: base is NFD (HFS+ disk form), target NFC (user-input form).
    base_nfd = Path("/data/projects/café")
    target_nfc = Path("/data/projects/café/scripts/episode_1.json")
    assert AgentAccessPolicy._is_protected_project_json(target_nfc, [base_nfd])

    # Normalization must not overmatch other project paths.
    other = Path("/data/projects/cafe_other/project.json")
    assert not AgentAccessPolicy._is_protected_project_json(other, [base_nfc])


def test_normalize_path_for_protected_compare_strips_windows_extended_prefix() -> None:
    """Windows ``\\\\?\\`` extended-length prefixes (returned by resolve for long/UNC paths) and ordinary forms normalize to one comparison key; mixed bases must not cause startswith mismatches. Helper-level unit test only; real Windows end-to-end validation is tracked separately."""
    norm = AgentAccessPolicy._normalize_path_for_protected_compare
    assert norm("\\\\?\\C:\\data\\projects\\demo") == norm("C:\\data\\projects\\demo")
    assert norm("\\\\?\\UNC\\server\\share\\proj") == norm("\\\\server\\share\\proj")
    # Ordinary paths are unaffected.
    assert norm("/data/projects/demo") == norm("/data/projects/demo")


def test_write_drafts_still_allowed(policy: AgentAccessPolicy) -> None:
    """Legitimate draft writes remain allowed (drafts/*.md and .json outside scripts)."""
    cwd = _cwd(policy)
    for relative in ("drafts/episode_1/script_plan_segments.md", "config_data.json"):
        allowed, _ = policy.check_path_access(str(cwd / relative), "Write", cwd, user_id=_USER_ID)
        assert allowed, f"{relative} 应允许"


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        ".env.production",
    ],
)
@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_sensitive_file_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """Deny both Read and Write for sensitive files; errors include "敏感文件"."""
    cwd = _cwd(policy)
    # Actual file existence does not affect denial (resolve() returns absolute paths even for missing files).
    target = policy.project_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason
    assert "敏感文件" in reason


@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_agent_profile_settings_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """Sensitive-file checks use the constructor's agent_profile_root rather than a hardcoded source-root path."""
    cwd = _cwd(policy)
    target = policy.agent_profile_root / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} agent_profile settings.json 应被拒"
    assert reason
    assert "敏感文件" in reason


def test_read_host_file_outside_project_root_denied(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """Host files outside project_root (~/.ssh, /etc, etc.) cannot be read through Read/Glob/Grep."""
    cwd = _cwd(policy)
    # tmp_path lies outside policy.project_root (project_root = tmp_path / "repo").
    outside = tmp_path / "host_fake_ssh"
    outside.mkdir()
    (outside / "id_rsa").write_text("secret", encoding="utf-8")
    for tool in ("Read", "Glob", "Grep"):
        allowed, reason = policy.check_path_access(str(outside / "id_rsa"), tool, cwd, user_id=_USER_ID)
        assert not allowed, f"{tool} 不应允许读 project_root 外的 host 文件"
        assert reason
        assert "项目根外" in reason


def test_sensitive_glob_pattern_does_not_overmatch(policy: AgentAccessPolicy) -> None:
    """`.env.*` must not accidentally reject legitimate files/directories named `.environment`."""
    cwd = _cwd(policy)
    legal = policy.project_root / ".environment"
    legal.parent.mkdir(parents=True, exist_ok=True)
    allowed, _ = policy.check_path_access(str(legal), "Read", cwd, user_id=_USER_ID)
    assert allowed, ".environment 是合法文件，不应被 `.env.*` glob 误伤"


# ============================================================
# Data-root reads default to denied: allow only current project and current user's memory.
# ============================================================


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
def test_read_of_data_root_outside_current_project_and_memory_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """When data root is inside repository root (default development layout), reference-material allowance must not admit its entries: deny registered system entries, old-layout remnants, unregistered entries and loose files under root/project directories."""
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
            allowed, reason = policy.check_path_access(str(target), tool, _cwd(policy), user_id=_USER_ID)
            assert not allowed, f"{tool} {target} 应被拒"
            assert reason


# ============================================================
# Kernel settings compilation (assertions moved from sandbox settings tests).
# ============================================================


def test_build_sandbox_settings_disabled_returns_only_enabled_false(tmp_path: Path) -> None:
    """sandbox_enabled=False (Windows fallback) returns only {"enabled": False}."""
    policy = _make_policy(tmp_path, sandbox_enabled=False)
    cwd = _projects_dir(policy) / "demo"
    assert policy.build_sandbox_settings(cwd) == {"enabled": False}


def test_build_sandbox_settings_enabled_returns_full_config(tmp_path: Path) -> None:
    """sandbox_enabled=True explicitly allows outbound and loopback access while retaining complete file boundaries."""
    policy = _make_policy(tmp_path, sandbox_enabled=True)
    cwd = _projects_dir(policy) / "demo"
    settings = policy.build_sandbox_settings(cwd)
    assert settings["enabled"] is True
    assert settings["autoAllowBashIfSandboxed"] is True
    assert settings["allowUnsandboxedCommands"] is False
    # Omitting network means zero preapprovals (all outbound blocked); explicitly use ``*`` to allow all.
    # allowLocalBinding controls loopback separately; allowedDomains cannot cover it.
    assert settings["network"] == {"allowedDomains": ["*"], "allowLocalBinding": True}
    assert "denyRead" in settings["filesystem"]
    assert str(cwd / "project.json") in settings["filesystem"]["denyWrite"]


def test_build_sandbox_settings_in_docker_enables_weaker_nested(tmp_path: Path) -> None:
    """Forward in_docker into enableWeakerNestedSandbox; default False outside Docker."""
    cwd = _projects_dir(_make_policy(tmp_path)) / "demo"
    assert _make_policy(tmp_path).build_sandbox_settings(cwd)["enableWeakerNestedSandbox"] is False
    assert _make_policy(tmp_path, in_docker=True).build_sandbox_settings(cwd)["enableWeakerNestedSandbox"] is True


def test_build_sandbox_settings_denies_write_to_project_json(policy: AgentAccessPolicy) -> None:
    """With sandbox enabled, denyWrite covers scripts/, project.json, edit_timelines/, drafts/, source/, .arcreel/trash/, media directories and versions/ at the Bash subprocess kernel layer."""
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
    """Deny all of ``drafts/`` rather than enumerating files: the list is compiled at session assembly, while episodes change at runtime. Per-file lists miss the main flow of planning episode N and editing it in the same session. Deliberately asymmetric with hooks, which deny formal script_plan only and leave drafts editable by built-in Edit."""
    cwd = _cwd(policy)
    deny_write = policy.build_sandbox_settings(cwd)["filesystem"]["denyWrite"]
    assert str(cwd / "drafts") in deny_write
    assert not any("episode_" in p for p in deny_write)


def test_build_sandbox_settings_deny_write_includes_resolved_paths(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """For symlink project_cwd (macOS /var↔/private/var or Linux project roots), denyWrite enumerates raw and resolved forms. String-based sandbox matching of raw only would miss Bash writes after symlink resolution. Match _check_write_access bases."""
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = _projects_dir(policy) / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    settings = policy.build_sandbox_settings(link_cwd)
    deny_write = settings["filesystem"]["denyWrite"]
    resolved_cwd = link_cwd.resolve()
    assert resolved_cwd != link_cwd
    # Register both raw and resolved forms.
    assert str(link_cwd / "scripts") in deny_write
    assert str(link_cwd / "project.json") in deny_write
    assert str(link_cwd / "drafts") in deny_write
    assert str(resolved_cwd / "scripts") in deny_write
    assert str(resolved_cwd / "project.json") in deny_write
    assert str(resolved_cwd / "drafts") in deny_write
    # Do not duplicate ordinary paths whose raw == resolved.
    assert len(deny_write) == len(set(deny_write))


def test_sandbox_denies_read_of_existing_sensitive_files_outside_data_root(tmp_path: Path) -> None:
    """Add individually existing sensitive files outside data root to denyRead, skipping missing ones."""
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
    """Bash bypasses read hooks: denyRead includes every top-level data-root entry outside project directories, including old/unregistered entries. Exclude the project directory so Bash reads/writes the current project normally."""
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
    """CLI skips missing deny paths and boundaries compile only at session startup. Precreate initially absent system directories (other users' memory on fresh installs, first uploaded credentials) so denial works from the start."""
    system_dirs = DataRootLayout(policy.data_root).system_dirs
    assert not any(system_dir.exists() for system_dir in system_dirs)

    deny_read = policy.build_sandbox_settings(_cwd(policy))["filesystem"]["denyRead"]

    for system_dir in system_dirs:
        assert system_dir.is_dir(), f"{system_dir} 应在会话启动时建出"
        assert str(system_dir) in deny_read


def test_filter_allowed_tools_strips_bash_family_when_sandbox_disabled(tmp_path: Path) -> None:
    """Strip Bash/BashOutput/KillBash when sandbox is disabled (can_use_tool allowlist); retain when enabled."""
    base = ["Skill", "Bash", "BashOutput", "KillBash", "Read"]
    enabled = _make_policy(tmp_path, sandbox_enabled=True)
    assert enabled.filter_allowed_tools(base) == base
    disabled = _make_policy(tmp_path, sandbox_enabled=False)
    assert disabled.filter_allowed_tools(base) == ["Skill", "Read"]


# ============================================================
# Bash secret stripping (env scrub), pure transformation
# ============================================================


def test_env_scrub_collects_pattern_matched_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beyond the fixed list, unset dynamically matched *_API_KEY / *_AUTH_TOKEN and similar patterns."""
    monkeypatch.setenv("GEMINI_CLI_IDE_AUTH_TOKEN", "abc")
    monkeypatch.setenv("RANDOM_VENDOR_API_KEY", "def")
    monkeypatch.setenv("PATH", "/usr/bin")  # Must not match.

    AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
    AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()
    try:
        keys = AgentAccessPolicy._collect_env_keys_to_scrub()
        assert "GEMINI_CLI_IDE_AUTH_TOKEN" in keys
        assert "RANDOM_VENDOR_API_KEY" in keys
        assert "PATH" not in keys
        # Fixed list.
        assert "ANTHROPIC_API_KEY" in keys
        assert "ARK_API_KEY" in keys
    finally:
        AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
        AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()


def test_wrap_bash_command_unsets_provider_keys(tmp_path: Path) -> None:
    """POSIX with sandbox enabled wraps command as ``env -u ANTHROPIC_* sh -c '<orig>'``."""
    from lib.config.env_keys import ANTHROPIC_ENV_KEYS

    policy = _make_policy(tmp_path)
    wrapped = policy.wrap_bash_command_for_env_scrub("env | grep ANTHROPIC")

    assert wrapped is not None
    # Unset every ANTHROPIC_* key.
    for key in ANTHROPIC_ENV_KEYS:
        assert f"-u {key}" in wrapped
    # shlex.quote wraps the original command inside sh -c.
    assert "sh -c " in wrapped
    assert "'env | grep ANTHROPIC'" in wrapped


def test_wrap_bash_command_preserves_short_lived_arcreel_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skill scripts may read their dedicated short-lived JWT in Bash subprocesses; strip other secret-like environment variables."""
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
    """Windows fallback: ``env -u``/``sh -c`` are POSIX-only and wrapping would start with ``env -u``, never matching the allowlist. Return None for no wrapping; can_use_tool matches the raw command."""
    policy = _make_policy(tmp_path, sandbox_enabled=False)
    assert policy.wrap_bash_command_for_env_scrub("python .claude/skills/x/scripts/run.py") is None


def test_wrap_bash_command_handles_single_quotes(tmp_path: Path) -> None:
    """Single quotes in commands must not break shell quote closure."""
    policy = _make_policy(tmp_path)
    wrapped = policy.wrap_bash_command_for_env_scrub("echo 'hello world'")
    assert wrapped is not None
    # shlex.quote escapes  'hello world'  as  'echo '"'"'hello world'"'"''
    assert wrapped.endswith("'\"'\"'hello world'\"'\"''")


def test_wrap_bash_command_passthrough_when_no_command(tmp_path: Path) -> None:
    """Do not wrap empty commands."""
    policy = _make_policy(tmp_path)
    assert policy.wrap_bash_command_for_env_scrub(None) is None
    assert policy.wrap_bash_command_for_env_scrub("   ") is None


# ============================================================
# Protected-write rule table: hooks and sandbox denyWrite project the same table.
# ============================================================


def test_protected_write_rules_project_new_rule_in_both_layers(
    policy: AgentAccessPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Add a protected path category with one PROTECTED_WRITE_RULES row: hook denial and sandbox denyWrite project the same table and synchronize. Separate declarations could leave a bypass in one layer."""
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
    # Hook layer: new rule immediately effective.
    allowed, reason = policy.check_path_access(str(cwd / "meta.lock"), "Write", cwd, user_id=_USER_ID)
    assert not allowed
    assert reason == synthetic.deny_message
    # Sandbox layer: denyWrite synchronizes from the same table.
    deny_write = policy.build_sandbox_settings(cwd)["filesystem"]["denyWrite"]
    assert str(cwd / "meta.lock") in deny_write
    # Existing rules unaffected.
    assert str(cwd / "project.json") in deny_write
    assert str(cwd / "scripts") in deny_write
    assert str(cwd / "drafts") in deny_write


# ============================================================
# Two-level memory-directory boundaries (ADR 0074).
# ============================================================


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_user_memory_dir_allowed_for_all_path_tools(policy: AgentAccessPolicy, tool: str) -> None:
    """User memory is outside cwd and within data root: explicitly allow all five tools, or cross-project read isolation and cwd-external write denial would reject it."""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).user_memory_dir(_USER_ID) / "MEMORY.md"
    allowed, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert allowed, f"{tool} 访问用户记忆应放行：{reason}"


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_user_memory_subdirectory_allowed(policy: AgentAccessPolicy, tool: str) -> None:
    """Allow the subtree rather than a single file; notes beyond the memory index also remain readable/writable."""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).user_memory_dir(_USER_ID) / "notes" / "style.md"
    allowed, _ = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert allowed


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_other_users_memory_dir_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """Allow only current user_id's subtree; deny other users' memory."""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).user_memory_dir("someone-else") / "MEMORY.md"
    allowed, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert not allowed, f"{tool} 访问他人记忆应被拒"
    assert reason


@pytest.mark.parametrize("tool", ["Read", "Write"])
def test_users_namespace_root_outside_memory_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """Allow ``<user_id>/memory/`` only, excluding other subtrees under the same user."""
    cwd = _cwd(policy)
    target = DataRootLayout(policy.data_root).users_dir / _USER_ID / "secrets" / "x.md"
    allowed, _ = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert not allowed


@pytest.mark.parametrize("tool", ["Read", "Write"])
def test_data_root_other_paths_still_denied_with_memory_allowance(policy: AgentAccessPolicy, tool: str) -> None:
    """Memory allowance does not spill into other data-root paths; deny other project directories."""
    cwd = _cwd(policy)
    allowed, _ = policy.check_path_access(str(_projects_dir(policy) / "other" / "x.json"), tool, cwd, user_id=_USER_ID)
    assert not allowed


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Write", "Edit"])
def test_project_memory_dir_allowed(policy: AgentAccessPolicy, tool: str) -> None:
    """Project memory is ``<cwd>/.arcreel/memory/``, allowed by cwd boundaries for all five tools."""
    cwd = _cwd(policy)
    target = project_memory_dir(cwd) / "MEMORY.md"
    allowed, reason = policy.check_path_access(str(target), tool, cwd, user_id=_USER_ID)
    assert allowed, f"{tool} 访问项目记忆应放行：{reason}"


def test_memory_dirs_still_reject_code_extensions(policy: AgentAccessPolicy) -> None:
    """Memory contains notes: relaxing extensions would hand executable scripts to sandboxed Bash."""
    cwd = _cwd(policy)
    for target in (
        project_memory_dir(cwd) / "hack.sh",
        DataRootLayout(policy.data_root).user_memory_dir(_USER_ID) / "hack.sh",
    ):
        allowed, reason = policy.check_path_access(str(target), "Write", cwd, user_id=_USER_ID)
        assert not allowed, f"{target} 应被代码扩展名规则拒"
        assert "代码" in reason


@pytest.mark.parametrize("bad_user_id", ["", "..", "../other", "a/b", "\x00"])
def test_invalid_user_id_denies_memory_instead_of_widening(policy: AgentAccessPolicy, bad_user_id: str) -> None:
    """Fail closed for user_id that is not a single path segment: allow no memory paths and never escape the data root."""
    cwd = _cwd(policy)
    escaped = DataRootLayout(policy.data_root).user_memory_dir("victim") / "MEMORY.md"
    allowed, _ = policy.check_path_access(str(escaped), "Write", cwd, user_id=bad_user_id)
    assert not allowed


@pytest.mark.parametrize(
    "in_docker",
    [pytest.param(False, id="macos-seatbelt-or-linux-bwrap"), pytest.param(True, id="docker-linux-bwrap")],
)
def test_build_sandbox_settings_gives_bash_no_access_to_user_memory(tmp_path: Path, in_docker: bool) -> None:
    """Kernel sandbox opens no user-memory exceptions: Bash reads and writes are denied, identically compiled on both platforms.

    bwrap compiles ``allowWrite`` into writable bind mounts overriding ``users/`` ``denyRead``; Seatbelt denies reads but allows writes. Built-in Read/Write/Edit/Glob/Grep run outside the kernel sandbox; hooks allow memory directories."""
    policy = _make_policy(tmp_path, in_docker=in_docker)
    cwd = _cwd(policy)
    layout = DataRootLayout(policy.data_root)
    filesystem = policy.build_sandbox_settings(cwd)["filesystem"]

    assert "allowWrite" not in filesystem
    assert str(layout.users_dir) in filesystem["denyRead"]
    # Built-in tools read/write user memory normally.
    memory_file = layout.user_memory_dir(_USER_ID) / "MEMORY.md"
    for tool in ("Read", "Write", "Edit", "Glob", "Grep"):
        allowed, reason = policy.check_path_access(str(memory_file), tool, cwd, user_id=_USER_ID)
        assert allowed, f"{tool} 访问用户记忆应放行：{reason}"


# ============================================================
# Data-root default read denial: case variants and search roots containing the data root.
# ============================================================


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
@pytest.mark.parametrize(
    "relative",
    ["PROJECTS/vertex_keys/key.json", "Projects/users/bob/memory/a.md", "PROJECTS/projects/other/project.json"],
)
def test_read_of_data_root_case_variant_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """On case-insensitive volumes, ``<仓库根>/PROJECTS`` is data root; repository-reference allowance must not admit case variants."""
    target = policy.project_root / relative
    allowed, reason = policy.check_path_access(str(target), tool, _cwd(policy), user_id=_USER_ID)
    assert not allowed
    assert reason


@pytest.mark.parametrize("tool", ["Glob", "Grep"])
def test_search_rooted_above_data_root_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """When data root is inside repository root, reject repository-root searches that recursively enter it; other repository reference directories remain searchable."""
    for search_root in (policy.project_root, policy.project_root / "."):
        allowed, reason = policy.check_path_access(str(search_root), tool, _cwd(policy), user_id=_USER_ID)
        assert not allowed
        assert reason
    allowed, _ = policy.check_path_access(str(policy.project_root / "lib"), tool, _cwd(policy), user_id=_USER_ID)
    assert allowed
