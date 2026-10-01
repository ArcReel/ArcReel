---
id: getting-started
title: Complete Getting Started Guide
sidebar_position: 1
---

# Complete Getting Started Guide {#getting-started}

This guide walks you through deploying ArcReel from scratch and creating your first AI video that you can preview, roll back, and export.

## What You Will Build {#what-you-will-build}

By the end of this guide, you will be able to:

1. Start ArcReel with Docker;
2. Sign in to the workspace and configure model providers;
3. Create a project from a novel, finished screenplay, or ad/short video;
4. Generate characters, scenes, props, storyboards, and video clips;
5. Monitor task status and costs;
6. Edit an episode, then render the final cut or export a Jianying draft for further post-production.

## Estimated Time and Cost {#time-and-cost}

- Initial deployment and configuration usually take about 10–20 minutes.
- Video generation time depends on the number of shots, provider queues, and the selected model.
- Text, image, video, and TTS calls may incur third-party API charges.
- ArcReel can estimate costs before generation and record actual usage afterward.

For your first project, use a small amount of content and 2–4 shots to validate the workflow. Do not submit a complete long-form project right away.

## 1. Prepare Your Environment {#prepare-environment}

### 1.1 System Requirements {#system-requirements}

Recommended environments:

- Linux
- macOS
- Windows + WSL2
- Docker Desktop

Recommended prerequisites:

- Docker
- Docker Compose
- Start with at least 2 GB of available memory
- A network environment that can access your selected model providers
- Enough disk space for images, videos, project archives, and logs

The default workflow uses remote model APIs and normally does not require a local GPU. If you connect a local model service, its requirements determine the necessary CPU, GPU, and memory resources.

### 1.2 Prepare Model Credentials {#prepare-credentials}

ArcReel uses two types of credentials for different purposes.

#### AI Assistant Credentials {#assistant-credentials}

These credentials power project conversations, content understanding, episode planning, script planning (including asset identification), and workflow orchestration.

You can use Anthropic's official service or a compatible service supported by ArcReel, and configure the Base URL and model name as needed.

#### Media and Text Generation Credentials {#media-and-text-credentials}

These credentials are used to call text, image, video, and TTS models.

ArcReel currently includes presets for these providers:

- Gemini
- Volcengine Ark
- Grok
- OpenAI
- Vidu
- DashScope
- MiniMax
- Kling
- Agnes
- Custom OpenAI-compatible or Google-compatible providers

Providers support different media types. A complete workflow usually requires at least:

- A working text generation capability;
- A working image generation capability;
- A working video generation capability;
- Optional TTS capability.

For detailed recommendations, see [Provider and Model Configuration](./providers.md).

> API keys are sensitive. Do not commit real keys to Git or expose them in issues, log screenshots, or public chat records.

## 2. Deploy ArcReel {#deploy-arcreel}

### 2.1 Clone the Repository {#clone-repository}

```bash
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel
```

### 2.2 Use the Default SQLite Deployment {#deploy-with-sqlite}

The default deployment is suitable for initial evaluation, personal creation, and light use.

```bash
cd deploy
cp .env.example .env
```

Edit `.env`:

```dotenv
AUTH_USERNAME=admin
AUTH_PASSWORD=set a strong password
AUTH_TOKEN_SECRET=set a long-lived random secret
```

You can generate `AUTH_TOKEN_SECRET` with this command:

```bash
openssl rand -hex 32
```

Start the service:

```bash
docker compose up -d
```

Check its status:

```bash
docker compose ps
docker compose logs --tail=100 arcreel
curl http://localhost:1241/health
```

After the health check succeeds, open this address in your browser:

```text
http://localhost:1241
```

> When `AUTH_PASSWORD` is empty, ArcReel automatically generates a password on first startup and writes it back to `.env`. For regular use, you should still set and securely store a strong password yourself.

### 2.3 Use the PostgreSQL Production Deployment {#deploy-with-postgresql}

PostgreSQL is recommended for long-running, concurrent, or production services. It improves concurrency, backups, and operations, but does not provide user isolation. Do not let mutually untrusted users share the same ArcReel instance.

Run these commands from the root of the ArcReel repository:

```bash
cd deploy/production
cp .env.example .env
```

Edit `.env`:

```dotenv
AUTH_USERNAME=admin
AUTH_PASSWORD=set a strong password
AUTH_TOKEN_SECRET=set a long-lived random secret
POSTGRES_PASSWORD=alphanumeric-only database password
```

Start the service:

```bash
docker compose up -d
docker compose ps
curl http://localhost:1241/health
```

For complete production deployment, upgrade, backup, and reverse proxy instructions, see [Deployment and Operations](../ops/deployment.md).

## 3. Complete the Initial Setup {#first-time-setup}

After signing in, first complete the onboarding tour and open the read-only demo project. It introduces the project lobby, workbench, AI assistant, and settings without consuming model credits.

Then open **Settings**.

### 3.1 Configure the AI Assistant {#configure-assistant}

Enter:

- An API key or credential;
- A Base URL;
- The primary model;
- Models for different tasks as needed.

After saving, send a simple message to verify the connection before starting any large batch of tasks.

### 3.2 Configure Media Providers {#configure-media-providers}

Configure at least one image provider and one video provider.

For your first project, consider this approach:

- Choose a more reliable image model for character designs;
- Choose an image model with a good balance of speed and cost for batch storyboards;
- Start with a fast or low-cost video tier to validate the visuals;
- After confirming the characters, composition, and direction of motion, switch to a higher-quality model.

### 3.3 Configure Concurrency and Cost {#configure-concurrency-and-cost}

Adjust these settings based on your provider quotas:

- RPM limits;
- Image concurrency;
- Video concurrency;
- Audio concurrency.

Settings that are too high may trigger provider rate limits, while settings that are too low will extend batch processing time. Start conservatively and increase them gradually after confirming that the workflow is stable.

## 4. Create Your First Project {#create-first-project}

On the project list page, click **New Project**.

### 4.1 Choose a Project Source {#choose-project-source}

#### Novel {#source-novel}

Best for projects that need to plan episodes, identify assets, and adapt a script from original source material.

For your first upload, use:

- One complete but short chapter;
- Or a 1,000–3,000-character excerpt from the story.

#### Finished Screenplay {#source-screenplay}

Best when you already have dialogue, voice-over, and scene structure that you want to preserve as closely as possible.

ArcReel builds characters and shots from the author's supplied content. It should not rewrite a finished screenplay as if it were an ordinary novel.

#### Ad or Short Video {#source-ad}

Best for merchandise showcases, shoppable videos, and short-form content with a defined target duration.

Prepare:

- Clear merchandise photos from multiple angles;
- Key selling points;
- The target audience;
- The desired duration and visual style.

### 4.2 Choose a Content Mode {#choose-content-mode}

- **Narration/Commentary**: Organizes segments around the reading pace, with voice-over and visuals at the center.
- **Drama**: Organizes shots around scenes, characters, and dialogue.
- **Ad / Short Video**: Organizes content around the target duration, selling points, and merchandise visuals.

For a detailed comparison, see [Workflows and Modes](./workflows.md).

## 5. Run the Workflow with the AI Assistant {#run-workflow-with-assistant}

Open the AI assistant panel on the right side of the project workbench.

Work through the process in stages instead of asking it to "generate the entire final video" at once.

### 5.1 Content Analysis {#content-analysis}

Ask the AI assistant to analyze:

- Main characters;
- Important scenes;
- Key props;
- Story conflicts;
- Suitable episode boundaries.

Review the results for:

- Duplicate or missing characters;
- Background extras incorrectly created as recurring characters;
- Key items that must remain consistent across shots;
- Complete episode boundaries that do not interrupt a key action.

### 5.2 Episodes and Script {#episodes-and-script}

After confirming the content analysis, generate the script for the episode or short video you are producing.

Review it for:

- A complete objective or emotional change in each episode;
- Dialogue and voice-over that remain faithful to the source;
- A shot count that fits the budget;
- A clear subject and action in every shot;
- Too many events packed into a single shot.

### 5.3 Character, Scene, and Prop Assets {#character-scene-prop-assets}

Generate the main character designs first, followed by reference images for key scenes and props.

Review them for:

- Accurate age, hairstyle, clothing, body type, and demeanor;
- Characters that are easy to distinguish from one another;
- Backgrounds or text in reference images that you do not want copied downstream;
- Accurate structure for merchandise, logos, and key props.

Do not batch-generate every storyboard before confirming the character assets, or any later rework will multiply.

### 5.4 Storyboard Images {#storyboard-images}

Generate a few storyboards to validate:

- Composition;
- Character positions;
- Shot type;
- Lighting and style;
- Character and prop consistency;
- Safe areas for portrait video.

After confirming the direction, generate them in batches.

### 5.5 Video Clips {#video-clips}

Choose a video generation mode based on the project:

- Storyboard mode (storyboard image-to-video, with multi-grid storyboards as an option);
- Reference-to-video mode.

With multi-grid storyboards enabled, Storyboard mode first generates several shots together on one or more multi-grid storyboards, then splits each grid into individual storyboard images. It is suitable for scenes that need stronger consistency across multiple shots.

Review the results for:

- Obvious character deformation;
- Actions that match the shot description;
- Continuous direction of motion across adjacent shots;
- An ending that connects cleanly to the next shot;
- Model-generated audio that conflicts with the post-production plan.

### 5.6 Narration and TTS {#narration-and-tts}

Narration projects can generate voice-over one segment at a time:

1. Select a TTS provider in Settings;
2. Set the voice and speaking rate;
3. Audition a short sample;
4. Generate in batches after confirming it;
5. Check proper nouns, character names, and pauses.

## 6. Monitor Tasks and Costs {#tasks-and-cost}

Generation tasks enter an asynchronous queue.

Monitor these states in the task panel:

- Queued;
- Running;
- Completed;
- Failed;
- Cancelled.

When a task fails, check the reason before repeatedly retrying it. Common causes include:

- An invalid API key;
- Insufficient quota;
- Provider rate limits;
- Parameters that the current model does not support;
- Network timeouts;
- An unsupported number or format of reference images.

On the Usage page, review:

- Estimated costs;
- Actual costs;
- Text, image, video, and audio usage;
- Statistics by provider and currency.

## 7. Edit and Render {#compose-and-export}

### 7.1 Edit and render the final cut {#compose-final-video}

After the videos are generated, switch to the **Edit** view at the top of the episode page. An episode's final form is defined by its edit timeline: let the Agent make a cut, or create an edit timeline from the script and then ask the Agent to adjust it in the chat. Every script layout uses the same entry points.

Click **Render** at the top of the edit view and select **Final cut** in the "Render · <edit timeline name>" dialog. An up-to-date final cut can be downloaded directly. When there is no final cut yet, or it is behind the edit timeline, start a render; the dialog shows its progress, and you can download the result when it finishes.

The dialog also selects the final cut version; each combination is a separate final cut:

- **Narration version**: available only to TTS voiceover projects, defaulting to **With narration**. A narration longer than its clip is mixed in at its full length; it is not shortened, later narrations are not shifted, and the issue list reports the overlap. Whatever runs past the end of the final cut is cut off.
- **Burn in subtitles**: checked by default. Subtitles are drawn into the video frames and cannot be turned off during playback; the font is the bundled Source Han Sans. When subtitles contain characters this font lacks, the issue list names those characters.

For background music, click the upload button next to the BGM track name in the edit view and upload MP3, WAV, or M4A audio (up to 100 MB). ArcReel measures the loudness once during upload and brings every BGM to the same loudness level; near-silent audio cannot be uploaded. BGM belongs to the whole project, so edit timelines in every episode can use it. After uploading, ask the Agent in the chat to place the BGM on the edit timeline; when the BGM is shorter than the timeline, the Agent places another section after it. Only one BGM plays at a time; any part past the end of the edit timeline is cut off with a 1-second fade-out at the cut. Both the final cut and the Jianying draft mix in the BGM. Uploaded BGM cannot be deleted from the page yet.

Before rendering, check:

- clip order;
- aspect ratio;
- the actual duration of each clip;
- alignment between narration and visuals;
- whether background music is needed;
- whether adjacent clips need transitions.

### 7.2 Export a Jianying draft {#export-jianying-draft}

To keep refining subtitles, audio tracks, transitions, and pacing in Jianying, select **Jianying draft** in the same render dialog. See the [Jianying Draft Export guide](./jianying-export.md) for detailed steps.

**Export project** in the top bar only downloads the whole project as an archive; it does not export final cuts or Jianying drafts.

## 8. First Project Completion Checklist {#first-project-checklist}

Do not define completion as "every asset has been generated once." At a minimum, make sure that:

- Character designs have been reviewed;
- References for key props and scenes have been reviewed;
- The shot structure is sound;
- Subjects and directions of motion connect across adjacent shots;
- Video clips have no obvious generation failures;
- Costs are within the expected range;
- At least one episode can be rendered successfully;
- The project has been backed up or archived at least once.

## 9. Next Steps {#next-steps}

- Read [Workflows and Modes](./workflows.md) to choose a more suitable production path;
- Read [Provider and Model Configuration](./providers.md) to establish quality, speed, and cost tiers;
- Read [Deployment and Operations](../ops/deployment.md) to configure a production environment and backups;
- If you encounter a problem, see the [FAQ](./faq.md).
