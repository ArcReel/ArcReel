# Agent Runtime Bidirectional Isolation Design

## Background

The project's Claude Code runtime configuration (`.claude/`, `CLAUDE.md`) mixes two scenarios:

1. **Agent mode** (Claude Agent SDK embedded in WebUI): video creation assistant for end users
2. **Development mode** (developer local CLI): coding assistant for developers

Current problems:
- `CLAUDE.md` serves both as system prompt and development guide, causing mutual interference
- Business Skills and third-party development Skills are mixed in `.claude/skills/`
- The agent loads the developer's `CLAUDE.md`, `CLAUDE.local.md`, and development-mode Skills

## Experimental Validation Findings

The following behaviors were validated through Claude Agent SDK experiments:

| Condition | Skill Discovery | CLAUDE.md Loading |
|------|-----------|---------------|
| `setting_sources=[]` | No skills discovered | Not loaded |
| `setting_sources=["project"]` + cwd has no git | Only scans cwd | Only loads cwd |
| `setting_sources=["project"]` + cwd inside git repo | Scans cwd + git root | Loads both levels |
| `setting_sources=["project"]` + cwd has symlink `.claude/` | Symlink target is discovered | Loaded |
| `add_dirs` parameter | Does not participate in skill discovery | Does not participate |

**Key inference**: Docker deployment has no git → `setting_sources=["project"]` only scans cwd → naturally zero leakage.

## Design

### Core Strategy

- `setting_sources=["project"]`: retain native Skill tool discovery capability
- Docker without git: natural isolation, cwd only discovers its own `.claude/`
- Symlink: project directory `.claude/` → `agent_runtime_profile/.claude/`
- System prompt: programmatically loaded from `agent_runtime_profile/CLAUDE.md`

### Directory Structure

```
agent_runtime_profile/                 # Agent-dedicated runtime environment (new)
├── CLAUDE.md                          # Agent system prompt
└── .claude/
    ├── skills/                        # Business Skills (migrated from .claude/skills/)
    │   ├── generate-characters/
    │   ├── generate-clues/
    │   ├── generate-storyboard/
    │   ├── generate-video/
    │   ├── generate-script/
    │   ├── compose-video/
    │   ├── manga-workflow/
    │   └── edit-script-items/
    └── agents/                        # Business Agents (migrated from .claude/agents/)
        ├── novel-to-narration-script.md
        └── novel-to-storyboard-script.md

.claude/                               # Returns to pure development mode
├── commands/
├── settings.local.json
├── plans/
└── skills/                            # Third-party development skills only (openspec-*, etc.)

CLAUDE.md                              # Slimmed down to pure developer codebase guide
```

### SessionManager Changes

#### `_build_options()` Changes

```python
ClaudeAgentOptions(
    cwd=str(project_cwd),
    setting_sources=["project"],       # Docker has no git, safe
    allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
    system_prompt=self._build_system_prompt(project_name),
    agents=self._load_agent_definitions(),  # Programmatic loading (double safety net)
    ...
)
```

#### `DEFAULT_ALLOWED_TOOLS` Corrections

Aligned with actual tool names in the SDK documentation:

```python
# Before
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Read", "Write", "Edit", "MultiEdit",
    "Bash", "Grep", "Glob", "LS", "AskUserQuestion",
]

# After
DEFAULT_ALLOWED_TOOLS = [
    "Skill", "Task", "Read", "Write", "Edit",
    "Bash", "Grep", "Glob", "AskUserQuestion",
]
```

Change notes:
- Added `Task` (subagent scheduling, actual SDK tool name)
- Removed `MultiEdit`, `LS` (do not exist in SDK documentation)

#### `_PATH_TOOLS` Corrections

```python
# Remove LS
_PATH_TOOLS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}
```

#### `_READONLY_DIRS` / `_READONLY_FILES` Updates

```python
_READONLY_DIRS = [
    "docs", "lib", "agent_runtime_profile",
    "scripts",
]
# Remove ".claude/skills", ".claude/agents", ".claude/plans"
# Add "agent_runtime_profile"

_READONLY_FILES = []
# Remove "CLAUDE.md" (agent does not need to read the developer docs at git root)
```

#### `_build_system_prompt()` Refactor

Reads the base prompt from `agent_runtime_profile/CLAUDE.md` (replacing the environment variable), then appends project context:

```python
def _build_system_prompt(self, project_name: str) -> str:
    # 1. Load base prompt from agent_runtime_profile/CLAUDE.md
    profile_prompt_path = self.project_root / "agent_runtime_profile" / "CLAUDE.md"
    base_prompt = profile_prompt_path.read_text(encoding="utf-8")

    # 2. Append project context (existing logic)
    ...
```

#### `_load_agent_definitions()` New Method

Scans `agent_runtime_profile/.claude/agents/*.md` and parses them into `dict[str, AgentDefinition]`.
Serves as a double safety net — even if `setting_sources=["project"]` fails to auto-discover agents, programmatic injection ensures agents are available.

### Symlink Creation on Project Creation

`ProjectManager` automatically creates a relative symlink when creating a new project:

```python
# projects/{name}/.claude → ../../agent_runtime_profile/.claude
symlink_path = project_dir / ".claude"
target = Path("../../agent_runtime_profile/.claude")
symlink_path.symlink_to(target)
```

Existing projects: a migration script is provided to create missing symlinks.

### Dockerfile Update

```dockerfile
# Before
COPY .claude/skills/ .claude/skills/
COPY .claude/agents/ .claude/agents/

# After
COPY agent_runtime_profile/ agent_runtime_profile/
```

### CLAUDE.md Slimdown

The current `CLAUDE.md` (git root) is split into:

| Content | Destination |
|------|------|
| Video specs, audio specs | `agent_runtime_profile/CLAUDE.md` |
| Content modes (narration/drama) | `agent_runtime_profile/CLAUDE.md` |
| Available Skills list | `agent_runtime_profile/CLAUDE.md` |
| Workflow (two modes) | `agent_runtime_profile/CLAUDE.md` |
| Video generation modes | `agent_runtime_profile/CLAUDE.md` |
| Script core fields | `agent_runtime_profile/CLAUDE.md` |
| Veo 3.1 technical reference | `agent_runtime_profile/CLAUDE.md` |
| project.json structure | `agent_runtime_profile/CLAUDE.md` |
| Project directory structure | `agent_runtime_profile/CLAUDE.md` |
| API usage | `agent_runtime_profile/CLAUDE.md` |
| Key principles | `agent_runtime_profile/CLAUDE.md` |
| Environment requirements | `agent_runtime_profile/CLAUDE.md` |
| API backend configuration | `agent_runtime_profile/CLAUDE.md` |
| General rules (language standards, etc.) | Retain relevant parts on both sides |

`CLAUDE.md` (git root) retains:
- Project introduction (one sentence)
- Architecture overview (references `CLAUDE.local.md`)
- Language standards (responses in Chinese)

### Compatibility

- Skill scripts' `lib/` import paths are unchanged (Python path is not affected by file location)
- Frontend API calls require no changes
- `generation_queue_client.py` path is unchanged
- Existing projects need to run the migration script once to create missing symlinks

## Isolation Effect Summary

### Docker Deployment (Production)

| Resource | Visible to Agent | Reason |
|------|--------------|------|
| `agent_runtime_profile/CLAUDE.md` | Yes (system_prompt injection) | Programmatic loading |
| `agent_runtime_profile/.claude/skills/` | Yes | Symlink + setting_sources |
| `agent_runtime_profile/.claude/agents/` | Yes | Programmatic + symlink |
| `CLAUDE.md` (git root) | No | Not packaged into image |
| `CLAUDE.local.md` | No | Not packaged into image |
| `.claude/skills/` (development mode) | No | Not packaged into image |
| Third-party Skills | No | Not packaged into image |

### Local Development (Developer CLI)

| Resource | Visible to Developer | Impact |
|------|--------------|------|
| `CLAUDE.md` (slimmed down) | Yes | Pure development guide, correct |
| `.claude/skills/` (third-party) | Yes | Development tools, correct |
| `agent_runtime_profile/` | Readable but not auto-loaded | No interference |
| Business Skills | Not auto-loaded | No noise |
