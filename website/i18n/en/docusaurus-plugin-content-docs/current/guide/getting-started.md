---
id: getting-started
title: Complete Getting Started Guide
sidebar_position: 1
---

# Complete Getting Started Guide {#getting-started}

This guide walks you through deploying ArcReel from scratch and creating your first AI video that you can preview, roll back, and export.

## What You Will Build {#what-you-will-build}

You’ll create a short project, make a short video, and render the final cut or export a Jianying draft from the editing view. If ArcReel is already running, you can go straight to [Initial Setup](#first-time-setup).

## Estimated Time and Cost {#time-and-cost}

For your first run, use a short chapter or script excerpt to try generating a few shots. Generation time depends on the model and provider queue. Text, image, video, and narration calls can incur charges. Review the estimate before submission and check [Usage Records](#tasks-and-cost) afterwards.

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

#### Agent Credentials {#assistant-credentials}

The Agent drives project conversations, content understanding, episode planning, script planning (including asset identification), and workflow orchestration.

You can use Anthropic's official service or a compatible service supported by ArcReel, and configure the Base URL and model name as needed. The Agent only accepts Anthropic Messages-compatible endpoints; see [Provider and Model Configuration · Agent Providers](./providers.md#agent-protocol) for the protocol requirement and external gateway options.

#### Media and Text Generation Credentials {#media-and-text-credentials}

Prepare text, image, and video generation for the project, plus narration audio if you need it. You can choose models from different providers. See [Provider and Model Configuration](./providers.md#capability-matrix) for where to check available models in Settings.

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

For a long-running or production instance, we recommend PostgreSQL. Follow the [production deployment instructions](../ops/deployment.md#postgresql-deployment), then return here for initial setup. Share the ArcReel instance only with users you trust.

## 3. Complete the Initial Setup {#first-time-setup}

After signing in, first complete the onboarding tour and open the read-only demo project. It introduces the project lobby, workbench, Agent, and settings without consuming model credits.

Then open **Settings**.

### 3.1 Configure the Agent {#configure-assistant}

Under Settings → ArcReel Agent, add a credential and activate it. Enter the API key, base URL, and model for the provider.

### 3.2 Configure Media Providers {#configure-media-providers}

Add the generation credentials under Settings → Providers, then choose text, image, and video models under Default Models. Before creating the project, check that the video model supports your [generation route](./workflows.md#video-production-routes). Generation settings and Agent credentials are saved separately. See [Configuration Layers](./providers.md#configuration-layers) for global and project precedence.

### 3.3 Configure Concurrency and Cost {#configure-concurrency-and-cost}

Run a small batch first so you can check the provider quota, turnaround time, and reference costs before scaling up. If you hit rate limits, check instance concurrency and account limits in [Provider Troubleshooting](./providers.md#frequent-rate-limits).

## 4. Create Your First Project {#create-first-project}

On the project list page, click **New Project**.

### 4.1 Choose a Project Source {#choose-project-source}

#### Novel {#source-novel}

Upload a short chapter as whole-source text, then plan the episodes to produce. You can also use a self-contained excerpt as the source text for one episode.

#### Finished Screenplay {#source-screenplay}

Use Drama mode and upload the script with its characters, scenes, and dialogue. Set the source type to Screenplay, then check dialogue and voice-over against the original during content confirmation.

#### Ad or Short Video {#source-ad}

In an Ad / Short Video project, enter the creative direction and target duration in the project overview. If the video features a product, add it and upload its source images.

### 4.2 Choose a Content Mode {#choose-content-mode}

The content mode determines how the script is structured. The generation route determines whether to generate storyboard images first or generate clips directly from references. Both choices are fixed after creation. See the [mode selection table](./workflows.md#mode-selection-table).

## 5. Run the Workflow with the Agent {#run-workflow-with-assistant}

Open the Agent panel on the right side of the project workbench. Send a short message and check that the Agent responds.

Work through the process in stages instead of asking it to "generate the entire final video" at once.

### 5.1 Content Analysis {#content-analysis}

Tell the Agent the aspect ratio, audience, and target duration. In Narration or Drama projects, confirm the source text and production scope in Episodes. An ad or short video can start from the creative direction and products in the overview.

### 5.2 Episodes and Script {#episodes-and-script}

After choosing **Stop planning** in Episodes, you can close the popover or leave the page. The current section finishes, and the episodes planned so far are kept. When replanning, choose **Stop generating** to keep the candidate generated so far. You can resume from the saved progress later.

Open the episode you want to produce. In Narration and Drama projects, run Script Planning, then check shot durations, dialogue, and newly identified assets in Content Review. Choose Confirm & continue to create the formal script. Ads and short videos generate a formal script directly. Check the result in the workspace. See [Content Structure](./workflows.md#stage-content-structure) for review criteria.

### 5.3 Character, Scene, and Prop Assets {#character-scene-prop-assets}

In Assets, find the characters, scenes, and props referenced by the script. Generate and review the primary asset sheets. You can also upload an existing picture as an asset sheet. Continue once key character looks and product details are ready.

### 5.4 Storyboard Images {#storyboard-images}

For Storyboard to Video, write visual prompts for a few shots and produce their storyboard images. Check the composition and assets before generating more. For Reference to Video, go to [Video Clips](#video-clips).

### 5.5 Video Clips {#video-clips}

Generate a clip for one storyboard or video unit first. Storyboard to Video uses the selected storyboard image as the starting frame. Reference to Video uses images and other references from the script. Review motion, duration, and audio before submitting the remaining clips. See [Generation Routes](./workflows.md#video-production-routes) for grids and reference selection.

Links to storyboards or video units in Agent replies can be opened in a new tab or pasted into the address bar. When the episode page loads, it selects the linked unit and scrolls it into view. If the link includes a timestamp, the preview seeks to that point.

### 5.6 Narration and TTS {#narration-and-tts}

In a Narration project, select TTS narration in Project Settings and choose the model, voice, and speed. Preview or generate a short sample to check pronunciation before generating the rest. Choose Post-production voiceover if you plan to add narration after export. See the [voice-over FAQ](./faq.md#voice-over-support).

## 6. Monitor Tasks and Costs {#tasks-and-cost}

Open Usage Records from the workspace header to see calls in progress, outcomes, usage, and reference costs. The project overview estimates costs from the current script. The provider bill gives the final charges.

If generation fails, expand the error and task details. When a finished video cannot be retrieved, use Retry Download if the task panel offers it. See [task states and recovery](./faq.md#task-states) for other failures.

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

Transitions in the preview may differ from the exported video. When you select a clip with a transition, its details remind you to check the exported video for the final effect.

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
