# Complete Getting-Started Guide

This guide walks you through converting a novel into a short video using ArcReel from scratch.

## What You Will Learn

1. **Environment setup** — obtain API keys
2. **Deploy the service** — via Docker
3. **Full workflow** — every step from novel to video
4. **Advanced tips** — regeneration, cost control, local development

## Estimated Time

- Environment setup: 10–20 minutes (first time only)
- Generate a 1-minute video: approximately 30 minutes

## Cost Estimate

ArcReel supports multiple providers (Gemini, Volcano Ark, Grok, OpenAI, and custom providers). The following uses Gemini as an example:

| Type | Model | Unit Price | Notes |
|------|-------|------------|-------|
| Image generation | Nano Banana Pro | $0.134/image (1K/2K) | High quality, ideal for character design |
| Image generation | Nano Banana 2 | $0.067/image (1K) | Faster and cheaper, ideal for storyboards |
| Video generation | Veo 3.1 | $0.40/sec (1080p with audio) | High quality |
| Video generation | Veo 3.1 Fast | $0.15/sec (1080p with audio) | Faster and cheaper |
| Video generation | Veo 3.1 Lite | Lower | Lightweight model, AI Studio only |

> **Example** (Gemini): a short video with 10 scenes (8 seconds each)
> - Images: 3 character designs (Pro) + 10 storyboards (Flash) = $0.40 + $0.67 = $1.07
> - Video: 80 sec x $0.15 (Fast mode) = $12
> - **Total approximately $13**

> **New-user benefit**: Google Cloud new users receive **$300 in free credits** valid for 90 days — enough to generate a large number of videos!
>
> For other provider pricing, refer to each provider's official pricing page. ArcReel provides real-time cost tracking in the settings page.

---

## Chapter 1: Environment Setup

### 1.1 Obtain Image / Video Provider API Keys

ArcReel supports multiple providers. **Configure at least one** to get started:

| Provider | Where to Get | Notes |
|----------|-------------|-------|
| **Gemini** (Google) | [AI Studio](https://aistudio.google.com/apikey) | Paid tier required; new users automatically get $300 credit |
| **Volcano Ark** | [Volcano Engine Console](https://console.volcengine.com/ark) | Billed per token / image (CNY) |
| **Grok** (xAI) | [xAI Console](https://console.x.ai/) | Billed per image / second (USD) |
| **OpenAI** | [OpenAI Platform](https://platform.openai.com/) | Billed per image / second (USD) |

You can also add a **custom provider** (any OpenAI-compatible / Google-compatible API) through the settings page after deployment.

> **Warning**: API keys are sensitive. Keep them safe — do not share them with others or commit them to a public repository.

### 1.2 Obtain an Anthropic API Key

ArcReel has a built-in AI assistant powered by the Claude Agent SDK, which handles script creation, intelligent conversation guidance, and other key functions.

**Option A: Use the official Anthropic API**

1. Visit [Anthropic Console](https://console.anthropic.com/)
2. Create an account and generate an API key
3. Configure it later in the Web UI settings page

**Option B: Use a third-party Anthropic-compatible API**

If you cannot access the Anthropic API directly, configure in the settings page:

- **Base URL** — enter the address of the relay service or compatible API
- **Model** — specify the model name (e.g. `claude-sonnet-4-6`)
- You can also configure separate default and subagent models for Haiku / Sonnet / Opus

### 1.3 Prepare a Server

**Server requirements:**

- OS: Linux / macOS / Windows WSL
- Memory: 2 GB+ recommended
- Docker and Docker Compose installed

**Install Docker (if not already installed):**

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Verify after re-logging in
docker --version
docker compose version
```

---

## Chapter 2: Deploy the Service

### 2.1 Download and Start

#### Option A: Default deployment (SQLite, recommended for getting started)

```bash
# 1. Clone the project
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel/deploy

# 2. Create the environment variable file
cp .env.example .env

# 3. Start the service
docker compose up -d
```

#### Option B: Production deployment (PostgreSQL, recommended for production use)

```bash
cd ArcReel/deploy/production

# Create the environment variable file (set POSTGRES_PASSWORD)
cp .env.example .env

docker compose up -d
```

After the containers finish starting, open a browser and go to **http://your-server-ip:1241**.

### 2.2 Initial Configuration

1. Log in with the default account (username `admin`, password set via `AUTH_PASSWORD` in `.env`; if not set, it is auto-generated and written back to `.env` on first launch)
2. Go to the **Settings** page (`/settings`)
3. Configure the **Anthropic API Key** (powers the AI assistant) — custom Base URL and model are supported
4. Configure at least one image/video **provider API key** (Gemini / Volcano Ark / Grok / OpenAI), or add a custom provider
5. Adjust model selection, rate limits, and other parameters as needed

> All configuration can be changed in the settings page — no need to edit configuration files manually.

---

## Chapter 3: Full Workflow

The following steps are completed in the Web UI workspace.

### 3.1 Create a Project

1. Click "New Project" on the project list page
2. Enter a project name (e.g. "My Novel")
3. Upload the novel text file (.txt format)

### 3.2 Generate a Storyboard Script

Open the AI assistant panel on the right side of the project workspace and let the assistant generate a script through conversation:

- The AI automatically analyzes the novel content and splits it into segments suitable for video
- Each segment includes a scene description, characters present, and key props/locations (clues)

**Review checkpoint**: Check that the script structure is sensible and that characters and clues are identified correctly.

### 3.3 Generate Character Design Images

The AI generates a design image for each character to maintain consistent appearance across all subsequent scenes.

**Review checkpoint**: Check that the character appearance matches the novel description. Regenerate if not satisfied.

### 3.4 Generate Clue Design Images

The AI generates reference images for important props and scene elements (e.g. keepsakes, specific locations).

**Review checkpoint**: Check that clue designs meet expectations.

### 3.5 Generate Storyboard Images

The AI generates a static image for each scene based on the script, automatically referencing character and clue design images to ensure consistency.

**Review checkpoint**: Check scene composition, character consistency, and atmosphere.

### 3.6 Generate Video Clips

Storyboard images serve as the starting frame; the selected video provider (Veo 3.1 / Seedance / Grok / Sora 2, etc.) generates 4–8 second dynamic video clips.

Generation tasks enter an asynchronous task queue. You can view progress in real time in the task monitor panel. Image and video channels run concurrently with independent limits, and RPM throttling ensures API quotas are not exceeded.

**Review checkpoint**: Preview each video clip and regenerate individual clips if not satisfied.

### 3.7 Compose the Final Video

All clips are concatenated by FFmpeg with transition effects and background music, producing the final video.

The default output is **9:16 portrait** format, suitable for publishing to short-video platforms.

---

## Chapter 4: Advanced Tips

### 4.1 Version History and Rollback

Every time you regenerate an asset, the system automatically saves the previous version. In the timeline view of the workspace, you can browse version history and roll back with one click.

### 4.2 Control Costs

**View cost statistics:**

The settings page shows API call counts and cost breakdowns.

**Tips for reducing spending:**

- Carefully review the output of each stage to reduce rework
- Generate a small number of scenes first to test results, then batch-generate once satisfied
- Using Fast mode for video generation saves approximately 60% in cost
- Use the Flash model for storyboard images and the Pro model for character design images

### 4.3 Project Import / Export

Projects can be packaged for archiving — useful for backup and migration:

- **Export**: package the entire project (including all assets) into an archive file
- **Import**: restore a project from an archive file

---

## Chapter 5: FAQ

### Q: Docker fails to start?

1. Confirm the Docker service is running: `systemctl status docker`
2. Check whether port 1241 is already in use: `ss -tlnp | grep 1241`
3. View container logs: `docker compose logs` (run in the corresponding `deploy/` or `deploy/production/` directory)

### Q: API calls fail?

1. Confirm the API key for the corresponding provider is correctly entered in the settings page
2. Gemini users must confirm the paid tier is enabled (the free tier does not support image/video generation)
3. Check that the server network can reach the corresponding provider's API service
4. Check the provider's console to see if API usage has exceeded the quota

### Q: The character looks different across scenes?

1. Make sure character design images are generated first
2. Check the quality of character design images and regenerate if not satisfactory
3. The system automatically uses character design images as reference to ensure consistency in subsequent scenes

### Q: Video generation is very slow?

Video generation typically takes 1–3 minutes per clip, which is normal. Factors that affect speed:

- Video duration (4 seconds vs. 8 seconds)
- API server load
- Network conditions

The task queue supports concurrent processing; multiple video clips can be generated at the same time.

### Q: Generation was interrupted — what do I do?

The task queue supports resuming from where it stopped. When you trigger generation again, the system automatically skips already-completed clips and processes only the remaining ones.

---

## Next Steps

Congratulations on completing the getting-started guide! Next you can:

- Check the [Google GenAI cost reference](google-genai-docs/Google-video-and-image-generation-cost-reference.md) and [Volcano Ark cost reference](ark-docs/volcano-ark-cost-reference.md) for detailed pricing
- Encountered a problem? Submit an [Issue](https://github.com/ArcReel/ArcReel/issues)
- Scan the QR code to join the Feishu group for help and updates:

<img src="assets/feishu-qr.png" alt="Feishu group QR code" width="280">

If you find the project useful, please give it a Star!
