<h1 align="center">
  <br>
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="frontend/public/android-chrome-maskable-512x512.png">
    <source media="(prefers-color-scheme: dark)" srcset="frontend/public/android-chrome-512x512.png">
    <img src="frontend/public/android-chrome-maskable-512x512.png" alt="ArcReel Logo" width="128" style="border-radius: 16px;">
  </picture>
  <br>
  ArcReel
  <br>
</h1>

<h4 align="center">Open-source AI Video Generation Workspace — Novel to Short Video, Powered by AI Agents</h4>

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/Quick_Start-blue?style=for-the-badge" alt="Quick Start"></a>
  <a href="https://github.com/ArcReel/ArcReel/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-green?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/ArcReel/ArcReel"><img src="https://img.shields.io/github/stars/ArcReel/ArcReel?style=for-the-badge" alt="Stars"></a>
  <a href="https://github.com/ArcReel/ArcReel/pkgs/container/arcreel"><img src="https://img.shields.io/badge/Docker-ghcr.io-blue?style=for-the-badge&logo=docker" alt="Docker"></a>
  <a href="https://github.com/ArcReel/ArcReel/actions/workflows/test.yml"><img src="https://img.shields.io/github/actions/workflow/status/ArcReel/ArcReel/test.yml?style=for-the-badge&label=Tests" alt="Tests"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black" alt="React">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Claude_Agent_SDK-Anthropic-191919?logo=anthropic&logoColor=white" alt="Claude Agent SDK">
  <img src="https://img.shields.io/badge/Gemini-Image_&_Video_&_Text-886FBF?logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Volcano_Engine-Image_&_Video_&_Text-FF6A00?logo=bytedance&logoColor=white" alt="Volcano Engine">
  <img src="https://img.shields.io/badge/Grok-Image_&_Video_&_Text-000000?logo=x&logoColor=white" alt="Grok">
  <img src="https://img.shields.io/badge/OpenAI-Image_&_Video_&_Text-74AA9C?logo=openai&logoColor=white" alt="OpenAI">
</p>

<p align="center">
  <img src="docs/assets/hero-screenshot.png" alt="ArcReel Workspace" width="800">
</p>

---

## Core Capabilities

<table>
<tr>
<td width="20%" align="center">
<h3>🤖 AI Agent Workflow</h3>
Built on the <strong>Claude Agent SDK</strong>, orchestrating Skill + focused Subagent multi-agent collaboration to automatically complete the full pipeline from script creation to video synthesis
</td>
<td width="20%" align="center">
<h3>🎨 Multi-Provider Image Generation</h3>
<strong>Gemini</strong>, <strong>Volcano Engine</strong>, <strong>Grok</strong>, <strong>OpenAI</strong>, and custom providers — character design images ensure character consistency, clue tracking maintains visual coherence of props/scenes across shots
</td>
<td width="20%" align="center">
<h3>🎬 Multi-Provider Video Generation</h3>
<strong>Veo 3.1</strong>, <strong>Seedance</strong>, <strong>Grok</strong>, <strong>Sora 2</strong>, and custom providers — switchable at global/project level
</td>
<td width="20%" align="center">
<h3>⚡ Async Task Queue</h3>
RPM rate limiting + independent Image/Video concurrency channels, lease-based scheduling, supports resume from breakpoint
</td>
<td width="20%" align="center">
<h3>🖥️ Visual Workspace</h3>
Web UI for project management, asset preview, version rollback, real-time SSE task tracking, with built-in AI assistant
</td>
</tr>
</table>

## Workflow

```mermaid
graph TD
    A["📖 Upload Novel"] --> B["📝 AI Agent Generates Storyboard Script"]
    B --> C["👤 Generate Character Design Images"]
    B --> D["🔑 Generate Clue Design Images"]
    C --> E["🖼️ Generate Storyboard Images"]
    D --> E
    E --> F["🎬 Generate Video Clips"]
    F --> G["🎞️ FFmpeg Synthesize Final Video"]
    F --> H["📦 Export CapCut Draft"]
```

## Quick Start

### Default Deployment (SQLite)

```bash
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel/deploy
cp .env.example .env
docker compose up -d
# Visit http://localhost:1241
```

### Production Deployment (PostgreSQL)

```bash
cd ArcReel/deploy/production
cp .env.example .env    # Set POSTGRES_PASSWORD
docker compose up -d
```

After the first startup, log in with the default account (username `admin`, password set via `AUTH_PASSWORD` in `.env`; if not set, it is auto-generated and written back to `.env` on first launch). Then go to the **Settings page** (`/settings`) to complete configuration:

1. **ArcReel Agent** — Configure your Anthropic API Key (powers the AI assistant), supports custom Base URL and model
2. **AI Image/Video Generation** — Configure at least one provider's API Key (Gemini / Volcano Engine / Grok / OpenAI), or add a custom provider

> 📖 For detailed steps, refer to the [Full Getting Started Guide](docs/getting-started.md)

## Features

- **Complete Production Pipeline** — Novel → Script → Character Design → Storyboard Images → Video Clips → Final Video, one-click orchestration
- **Multi-Agent Architecture** — Orchestration Skill detects project state and automatically dispatches focused Subagents; each Subagent completes one task independently and returns a summary
- **Multi-Provider Support** — Image/video/text generation supports Gemini, Volcano Engine, Grok, and OpenAI as four preset providers, switchable at global/project level
- **Custom Providers** — Connect any OpenAI-compatible / Google-compatible API (e.g., Ollama, vLLM, third-party relays); automatically discovers available models and assigns media types, with the same feature parity as preset providers
- **Two Content Modes** — Narration mode splits segments by reading rhythm; Drama animation mode organizes by scene/dialogue structure
- **Progressive Episode Planning** — Human-AI collaborative segmentation of long novels: peek detection → Agent suggests breakpoints → user confirms → physical split → produce on demand
- **Style Reference Images** — Upload style images; AI automatically analyzes and uniformly applies to all image generation, ensuring visual consistency across the project
- **Character Consistency** — AI first generates character design images; all subsequent storyboards and videos reference that design
- **Clue Tracking** — Key props and scene elements marked as "clues" to maintain visual coherence across shots
- **Version History** — Automatically saves historical versions on each regeneration, supports one-click rollback
- **Multi-Provider Cost Tracking** — Image/video/text all included in cost calculation, billed by provider-specific strategies, with separate tallies for different currencies
- **Cost Estimation** — Estimate project/episode/scene costs before generation; three-level drill-down showing estimated vs. actual cost comparison
- **CapCut Draft Export** — Export CapCut draft ZIP per episode, supports CapCut 5.x / 6+ ([Operation Guide](docs/jianying-export-guide.md))
- **Project Import/Export** — Archive entire projects for easy backup and migration

## Provider Support

ArcReel supports multiple preset providers and custom providers through a unified `ImageBackend` / `VideoBackend` / `TextBackend` protocol, switchable at global or project level:

### Image Providers

| Provider | Available Models | Capabilities | Billing |
|--------|----------|------|----------|
| **Gemini** (Google) | Nano Banana 2, Nano Banana Pro | Text-to-image, Image-to-image (multi-reference) | Table lookup by resolution (USD) |
| **Volcano Engine** | Seedream 5.0, Seedream 5.0 Lite, Seedream 4.5, Seedream 4.0 | Text-to-image, Image-to-image | Per image (CNY) |
| **Grok** (xAI) | Grok Imagine Image, Grok Imagine Image Pro | Text-to-image, Image-to-image | Per image (USD) |
| **OpenAI** | GPT Image 1.5, GPT Image 1 Mini | Text-to-image, Image-to-image (multi-reference) | Per image (USD) |

### Video Providers

| Provider | Available Models | Capabilities | Billing |
|--------|----------|------|----------|
| **Gemini** (Google) | Veo 3.1, Veo 3.1 Fast, Veo 3.1 Lite | Text-to-video, Image-to-video, Video extension, Negative prompts | Table lookup by resolution × duration (USD) |
| **Volcano Engine** | Seedance 2.0, Seedance 2.0 Fast, Seedance 1.5 Pro | Text-to-video, Image-to-video, Video extension, Audio generation, Seed control, Offline inference | Per token usage (CNY) |
| **Grok** (xAI) | Grok Imagine Video | Text-to-video, Image-to-video | Per second (USD) |
| **OpenAI** | Sora 2, Sora 2 Pro | Text-to-video, Image-to-video | Per second (USD) |

### Text Providers

| Provider | Available Models | Capabilities | Billing |
|--------|----------|------|----------|
| **Gemini** (Google) | Gemini 3.1 Flash, Gemini 3.1 Flash Lite, Gemini 3 Pro | Text generation, Structured output, Visual understanding | Per token usage (USD) |
| **Volcano Engine** | Doubao Seed series | Text generation, Structured output, Visual understanding | Per token usage (CNY) |
| **Grok** (xAI) | Grok 4.20, Grok 4.1 Fast series | Text generation, Structured output, Visual understanding | Per token usage (USD) |
| **OpenAI** | GPT-5.4, GPT-5.4 Mini, GPT-5.4 Nano | Text generation, Structured output, Visual understanding | Per token usage (USD) |

### Custom Providers

In addition to preset providers, you can connect any **OpenAI-compatible** or **Google-compatible** API:

- Add a custom provider on the settings page by entering a Base URL and API Key
- Automatically calls `/v1/models` to discover available models, inferring media type (image/video/text) from model names
- Full feature parity with preset providers: global/project-level switching, cost tracking, version management

Provider selection priority: project-level settings > global defaults. When switching providers, common settings (resolution, aspect ratio, audio, etc.) carry over directly; provider-specific parameters are retained.

## Community

Scan the QR code to join the Lark community group for help and latest updates:

<p align="center">
  <img src="docs/assets/feishu-qr.png" alt="Lark Community Group QR Code" width="280">
</p>

## AI Assistant Architecture

ArcReel's AI assistant is built on the Claude Agent SDK, using an **Orchestration Skill + Focused Subagent** multi-agent architecture:

```mermaid
flowchart TD
    User["User Conversation"] --> Main["Main Agent"]
    Main --> MW["manga-workflow<br/>Orchestration Skill"]
    MW -->|"State Detection"| PJ["Read project.json<br/>+ Filesystem"]
    MW -->|"dispatch"| SA1["analyze-characters-clues<br/>Global Character/Clue Extraction"]
    MW -->|"dispatch"| SA2["split-narration-segments<br/>Narration Mode Segment Splitting"]
    MW -->|"dispatch"| SA3["normalize-drama-script<br/>Drama Animation Normalization"]
    MW -->|"dispatch"| SA4["create-episode-script<br/>JSON Script Generation"]
    MW -->|"dispatch"| SA5["Asset Generation Subagent<br/>Character/Clue/Storyboard/Video"]
    SA1 -->|"Summary"| Main
    SA4 -->|"Summary"| Main
    Main -->|"Display Results<br/>Await Confirmation"| User
```

**Core Design Principles**:

- **Orchestration Skill (manga-workflow)** — Has state detection capability, automatically determines the current project phase (character design / episode planning / preprocessing / script generation / asset generation), dispatches the corresponding Subagent, supports entry from any phase and resumption from interruption
- **Focused Subagent** — Each Subagent completes only one task before returning; large context such as the novel text remains inside the Subagent, while the main Agent only receives a refined summary, protecting context space
- **Skill vs Subagent Boundary** — Skills handle deterministic script execution (API calls, file generation); Subagents handle tasks requiring reasoning and analysis (character extraction, script normalization)
- **Inter-phase Confirmation** — After each Subagent returns, the main Agent presents a result summary to the user and waits for confirmation before proceeding to the next phase

## OpenClaw Integration

ArcReel supports invocation via external AI Agent platforms such as [OpenClaw](https://openclaw.ai), enabling natural-language-driven video creation:

1. Generate an API Key on the ArcReel settings page (with `arc-` prefix)
2. Load ArcReel's Skill definition in OpenClaw (access `http://your-domain/skill.md` to retrieve automatically)
3. Create projects, generate scripts, and produce videos through OpenClaw conversation

Technical implementation: API Key authentication (Bearer Token) + synchronous Agent conversation endpoint (`POST /api/v1/agent/chat`), internally connected to the SSE streaming assistant, collecting and returning the complete response.

## Technical Architecture

```mermaid
flowchart TB
    subgraph UI["Web UI — React 19"]
        U1["Project Management"] ~~~ U2["Asset Preview"] ~~~ U3["AI Assistant"] ~~~ U4["Task Monitoring"]
    end

    subgraph Server["FastAPI Server"]
        S1["REST API<br/>Route Dispatch"] ~~~ S2["Agent Runtime<br/>Claude Agent SDK"]
        S3["SSE Stream<br/>Real-time Status Push"] ~~~ S4["Auth<br/>JWT + API Key"]
    end

    subgraph Core["Core Library"]
        C1["VideoBackend Abstraction<br/>Gemini · Volcano Engine · Grok · OpenAI · Custom"] ~~~ C2["ImageBackend Abstraction<br/>Gemini · Volcano Engine · Grok · OpenAI · Custom"]
        C5["TextBackend Abstraction<br/>Gemini · Volcano Engine · Grok · OpenAI · Custom"] ~~~ C3["GenerationQueue<br/>RPM Rate Limiting · Image/Video Channels"]
        C4["ProjectManager<br/>Filesystem + Version Management"]
    end

    subgraph Data["Data Layer"]
        D1["SQLAlchemy 2.0 Async ORM"] ~~~ D2["SQLite / PostgreSQL"]
        D3["Alembic Migrations"] ~~~ D4["UsageTracker<br/>Multi-Provider Cost Tracking"]
    end

    UI --> Server --> Core --> Data
```

## Tech Stack

| Layer | Technology |
|------|------|
| **Frontend** | React 19, TypeScript, Tailwind CSS 4, wouter, zustand, Framer Motion, Vite |
| **Backend** | FastAPI, Python 3.12+, uvicorn, Pydantic 2 |
| **AI Agents** | Claude Agent SDK (Skill + Subagent multi-agent architecture) |
| **Image Generation** | Gemini (`google-genai`), Volcano Engine (`volcengine-python-sdk[ark]`), Grok (`xai-sdk`), OpenAI (`openai`) |
| **Video Generation** | Gemini Veo 3.1 (`google-genai`), Volcano Engine Seedance 2.0/1.5 (`volcengine-python-sdk[ark]`), Grok (`xai-sdk`), OpenAI Sora 2 (`openai`) |
| **Text Generation** | Gemini (`google-genai`), Volcano Engine (`volcengine-python-sdk[ark]`), Grok (`xai-sdk`), OpenAI (`openai`), Instructor (structured output fallback) |
| **Media Processing** | FFmpeg, Pillow |
| **ORM & Database** | SQLAlchemy 2.0 (async), Alembic, aiosqlite, asyncpg — SQLite (default) / PostgreSQL (production) |
| **Authentication** | JWT (`pyjwt`), API Key (SHA-256 hash), Argon2 password hashing (`pwdlib`) |
| **Deployment** | Docker, Docker Compose (`deploy/` default, `deploy/production/` with PostgreSQL) |

## Documentation

- 📖 [Full Getting Started Guide](docs/getting-started.md) — Step-by-step guide from scratch
- 📦 [CapCut Draft Export Guide](docs/jianying-export-guide.md) — Import video clips into CapCut desktop for secondary editing
- 💰 [Google GenAI Cost Reference](docs/google-genai-docs/Google视频&图片生成费用参考.md) — Gemini image / Veo video generation cost reference
- 💰 [Volcano Engine Cost Reference](docs/ark-docs/火山方舟费用参考.md) — Volcano Engine video / image / text model cost reference

## Contributing

Contributions, bug reports, and feature suggestions are welcome! Please refer to the [Contributing Guide](CONTRIBUTING.md) for local development environment setup, testing, and code standards.

## License

[AGPL-3.0](LICENSE)

---

<p align="center">
  If you find this project useful, please give it a ⭐ Star!
</p>
