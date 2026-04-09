# AI Video Generation Workspace

---

## Important General Rules

The following rules apply to all operations across the entire project:

### Language Policy
- **Respond to users in English**: all replies, reasoning, task lists, and planning documents must use English
- **Video content must be in Chinese**: all generated video dialogues, narrations, and subtitles use Chinese
- **Documents use English**: all Markdown files are written in English
- **Prompts use Chinese**: image generation/video generation prompts should be written in Chinese

### Video Specifications
- **Aspect ratio**: determined by the project's `aspect_ratio` configuration; no need to specify in the prompt
  - Narration + visuals mode default: **9:16 portrait**
  - Drama animation mode default: 16:9 landscape
- **Single segment/scene duration**: determined by video model capability and the project's `default_duration` configuration
  - Narration + visuals mode default: **4 seconds**
  - Drama animation mode default: 8 seconds
- **Image resolution**: 1K
- **Video resolution**: 1080p
- **Generation method**: each segment/scene generated independently, using the storyboard image as the starting frame

> **About the extend feature**: Veo 3.1 extend is only for extending a single segment/scene,
> each time adding a fixed +7 seconds; it is not suitable for connecting different shots. Different segments/scenes are concatenated with ffmpeg.

### Audio Policy
- **BGM automatically excluded**: background music is automatically excluded via the `negative_prompt` API parameter

### Script Invocation
- **Skill internal scripts**: each skill's executable scripts are located in the `agent_runtime_profile/.claude/skills/{skill-name}/scripts/` directory
- **Virtual environment**: activated by default; scripts do not need to manually activate .venv

---

## Content Modes

The system supports two content modes (narration + visuals / drama animation), switched via the `content_mode` field in `project.json`.

> Detailed specifications (aspect ratio, duration, data structure, preprocessing Agent, etc.) are in `.claude/references/content-modes.md`.

---

## Project Structure

- `projects/{project-name}` - workspace for video projects
- `lib/` - shared Python library (Gemini API wrappers, project management)
- `agent_runtime_profile/.claude/skills/` - available skills

## Architecture: Orchestration Skill + Focused Subagents

```
Main Agent (orchestration layer — extremely lightweight)
  │  Holds only: project status summary + user conversation history
  │  Responsibilities: status detection, flow decisions, user confirmation, dispatch subagents
  │
  ├─ dispatch → analyze-characters-clues     global character/clue extraction
  ├─ dispatch → split-narration-segments     narration mode segment splitting
  ├─ dispatch → normalize-drama-script       drama mode script normalization
  ├─ dispatch → create-episode-script        JSON script generation (preloads generate-script skill)
  └─ dispatch → generate-assets             asset generation (characters/clues/storyboard/video)
```

### Skill/Agent Boundary Principles

| Type | Purpose | Examples |
|------|------|------|
| **Subagent (focused task)** | Requires large context or reasoning analysis → protects main agent context | analyze-characters-clues, split-narration-segments |
| **Skill (called within subagent)** | Deterministic script execution → API calls, file generation | generate-script, generate-characters |
| **Main Agent direct operations** | Lightweight operations only | read project status, simple file operations, user interaction |

### Key Constraints

- **Subagents cannot spawn subagents**: multi-step workflows can only be chained by the main agent dispatching sequentially
- **Novel text never enters main agent**: read by the subagent independently; the main agent only passes file paths
- **Each subagent has one focused task**: returns immediately upon completion; does not perform multi-step user confirmations internally

### Responsibility Boundaries

- **No coding**: must not create or modify any code files (.py/.js/.sh etc.); data processing must be done through existing skill scripts
- **Code bug reporting**: if a skill script has a clear code bug (not a parameter or environment issue), report the error to the user and suggest filing feedback with the developers

## Available Skills

| Skill | Trigger Command | Function |
|-------|---------|------|
| manga-workflow | `/manga-workflow` | Orchestration skill: status detection + subagent dispatch + user confirmation |
| manage-project | — | Project management toolset: episode splitting (peek+split), batch character/clue writing |
| generate-script | — | Generate JSON script using Gemini (called by subagent) |
| generate-characters | `/generate-characters` | Generate character design sheets |
| generate-clues | `/generate-clues` | Generate clue design sheets |
| generate-storyboard | `/generate-storyboard` | Generate storyboard images |
| generate-video | `/generate-video` | Generate video clips |

## Quick Start

New users should use `/manga-workflow` to begin the complete video creation workflow.

## Workflow Overview

The `/manga-workflow` orchestration skill automatically advances through the following phases (waits for user confirmation after each phase):

1. **Project Setup**: create project, upload novel, generate project overview
2. **Global character/clue design** → dispatch `analyze-characters-clues` subagent
3. **Episode planning** → main agent directly executes peek+split (manage-project toolset)
4. **Single episode preprocessing** → dispatch `split-narration-segments` (narration) or `normalize-drama-script` (drama)
5. **JSON script generation** → dispatch `create-episode-script` subagent
6. **Character design + clue design** (can run in parallel) → dispatch `generate-assets` subagent
7. **Storyboard generation** → dispatch `generate-assets` subagent
8. **Video generation** → dispatch `generate-assets` subagent

The workflow supports **flexible entry points**: status detection automatically locates the first incomplete phase, supporting resume after interruption.
After video generation is complete, users can export to CapCut draft in the Web interface.

## Key Principles

- **Character consistency**: each scene uses the storyboard image as the starting frame, ensuring consistent character appearance
- **Clue consistency**: important items and environmental elements are fixed through the `clues` mechanism, ensuring cross-scene consistency
- **Storyboard continuity**: use segment_break markers for scene transition points; transition effects can be added later
- **Quality control**: check quality after each scene is generated; unsatisfactory scenes can be individually regenerated

## Project Directory Structure

```
projects/{project-name}/
├── project.json       # project metadata (characters, clues, episodes, style)
├── source/            # original novel content
├── scripts/           # storyboard scripts (JSON)
├── characters/        # character design sheets
├── clues/             # clue design sheets
├── storyboards/       # storyboard images
├── videos/            # generated videos
└── output/            # final output
```

### Core Fields in project.json

- `title`, `content_mode` (`narration`/`drama`), `style`, `style_description`
- `overview`: project overview (synopsis, genre, theme, world_setting)
- `episodes`: episode core metadata (episode, title, script_file)
- `characters`: complete character definitions (description, character_sheet, voice_style)
- `clues`: complete clue definitions (type, description, importance, clue_sheet)

### Data Layering Principles

- Complete character/clue definitions are **stored only in project.json**; scripts only reference names
- Stats fields like `scenes_count`, `status`, `progress` are **computed on read** by StatusCalculator, not stored
- Episode metadata (episode/title/script_file) is **synced on write** when the script is saved
