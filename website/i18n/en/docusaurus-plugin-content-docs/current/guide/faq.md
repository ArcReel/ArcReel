---
id: faq
title: Frequently Asked Questions
sidebar_position: 5
---

# Frequently Asked Questions {#faq}

This page covers the most common installation, configuration, production, and troubleshooting questions for the current version of ArcReel. If this is your first time using ArcReel, start with the [complete getting-started tutorial](./getting-started.md). The Settings page is the source of truth for current provider and model capabilities.

## What capability boundaries should I understand before using ArcReel? {#capability-boundaries}

- Media generation depends on third-party model services. Generation speed, availability, content policies, and costs are controlled by providers.
- Long-form content still requires human review of episode boundaries, character assets, and key plot decisions. ArcReel is designed to empower creators, not eliminate review entirely.
- Video models differ in their support for reference-image counts, video duration, start and end frames, audio, and regional availability.
- Native Windows can run parts of the basic workflow, but POSIX capabilities such as the Agent sandbox degrade. Prefer Linux, macOS, or WSL2.
- The service is intended for local use by default. Do not expose an unprotected port to the public Internet.

## Installation, startup, and updates {#install-deploy-update}

### What is the recommended installation method? Can ArcReel run directly on Windows? {#how-to-install}

Run ArcReel from source on Linux, macOS, or WSL2:

```bash
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel
uv sync
cd frontend && pnpm install && cd ..
cp .env.example .env
./scripts/dev.sh
```

After startup, open `http://localhost:5173`.

Native Windows can run basic workflows such as project creation, but the Agent sandbox degrades, so WSL2 is recommended. Local Linux and macOS environments require working system sandbox tools.

### What should I do if the local service does not open or keeps exiting? {#local-service-wont-start}

Check the terminal running `./scripts/dev.sh`, then run:

```bash
curl http://localhost:1241/health
```

Check the following in order:

1. Confirm that ports `1241` and `5173` are not already in use.
2. Confirm that the root `.env` exists and is readable and writable by the current user.
3. Check whether the logs contain `SANDBOX_UNAVAILABLE`, `SANDBOX_BWRAP_BROKEN`, or more specific remediation guidance.
4. Check whether provider API keys were mistakenly placed in `.env`. Provider credentials must be saved in the Web Settings page, or the service will refuse to start.

### What are the default login credentials? What should I do if I forget the password? {#default-login}

The default username is `admin`; you can change it in `.env` with `AUTH_USERNAME`. If `AUTH_PASSWORD` is empty, ArcReel generates a password on first startup and writes it back to the root `.env`. The plaintext password is not printed to the logs.

If you forget the password, update `AUTH_PASSWORD` in `.env`, then restart `./scripts/dev.sh`. Do not disable authentication on an untrusted network.

### How do I update ArcReel? Will an update delete my projects? {#source-update}

Back up project data and the database, then run these commands from the repository root:

```bash
git pull --ff-only
uv sync
cd frontend && pnpm install --frozen-lockfile && cd ..
uv run alembic upgrade head
```

ArcReel automatically runs database and project-structure migrations at startup. A normal update does not intentionally delete project data, but you should still back up the project directory, database, and credential files before updating.

The About section of the Settings page can check for a new version and open its release page, but it does not upgrade the server from the Web UI.

### Where are projects and configuration stored? How do I back them up or migrate them? {#where-is-data}

The main data for a default local run is stored in the repository root:

- `projects/`: projects, assets, and the default SQLite database
- `.env`: login and runtime configuration

When using PostgreSQL, back up the PostgreSQL database as well. A complete backup must cover the project directory, database, and required credentials. Use `pg_dump` / `pg_restore` for PostgreSQL. Copy SQLite only after stopping the service, or use SQLite's online backup mechanism.

A project ZIP from the Web UI is suitable for migrating an individual project, but it does not include global provider configuration, account configuration, task records, cost records, or Agent sessions. It is not a substitute for a full-instance backup.

## Initial configuration and the AI assistant {#setup-and-assistant}

### Which credentials must I configure before production? {#required-credentials}

ArcReel uses two independent sets of configuration for the AI assistant and content generation:

- The **Agent provider** handles conversations, source-text analysis, and production orchestration. It requires a working, active Agent credential.
- **Generation providers** handle text, image, video, and speech generation. You must configure providers and models for the modalities your project actually uses.

Configuring only an Agent credential does not automatically provide image- or video-generation capabilities. Configuring only generation providers does not let the in-project AI assistant start. Project-level generation model settings override global defaults, so troubleshooting should confirm which provider and model the project actually selected.

Preset generation providers and Agent providers can each store multiple credentials, but ArcReel uses only the one currently marked active at runtime and does not rotate them automatically. Testing a credential does not activate it. Each custom provider currently stores one API Key.

### How do I make the AI assistant start or resume production? {#start-or-resume-assistant}

Open the AI assistant on the right side of the project and simply ask it to “start production” or “continue production.” The assistant checks the project's current state, resumes from the first incomplete stage, and waits for confirmation when the screenplay, assets, or storyboards need review.

If the assistant reports that you are not logged in, cannot start, or have no available model, first open Settings and confirm that an Agent credential is saved, its message-call probe succeeds, and it is active. A model-discovery warning does not necessarily block message calls, but you should verify the actual model ID. You do not need to sign in to a Claude web account separately in the assistant panel.

### What should I do about `Failed to start Claude Code`, a startup timeout, or `Control request timeout`? {#assistant-startup-failure}

These messages mean that the Agent process did not start normally or could not initialize in time. They do not indicate an error in the screenplay content. Check the following in order:

1. Confirm that the Agent credential is active and that the model ID exactly matches an ID actually exposed by the service.
2. Confirm that the local machine can reach the Agent API and that its proxy, DNS, and TLS configuration works.
3. Confirm that the Agent sandbox passes its startup checks in WSL or the local environment.
4. Download diagnostic logs from the About section of Settings and inspect the specific upstream status code near the time of the error.

A successful connection test proves only that a minimal request succeeded. Long sessions may still be affected by quotas, context limits, rate limits, or proxy timeouts.

## Providers, models, and APIs {#providers-models-api}

### Which providers and models does ArcReel support? {#supported-providers}

The preset providers, model list, and capability markers currently shown in Settings define the supported scope. Support for a provider does not mean that all of its models, account regions, and input forms are available. Image, video, text, speech, reference-image, end-frame, duration, and resolution capabilities are evaluated for each specific model.

Do not infer capabilities from the model name alone. After adding or changing a model, reconfirm its media type, call endpoint, and account permissions.

### How should I enter the Base URL? {#base-url-format}

Prefer a preset provider so ArcReel can use its built-in address. For a custom provider, enter the protocol root URL specified in the provider's API documentation. Do not enter a console webpage address or blindly append the same `/v1` or `/v1/messages` suffix to every URL.

If the response is `404 page not found`, usually you should verify:

1. Which discovery protocol and call protocol are currently selected.
2. Whether the Base URL points to the correct region and API root path.
3. Whether the service actually implements the corresponding model-list and generation endpoints.

### How do I connect a custom provider? {#custom-provider-setup}

Add a custom provider in Settings, enter its Base URL, API Key, and model-discovery protocol, then fetch the model list. If discovery fails, you can add models manually, but you must still verify each model's media type and call endpoint before enabling it and selecting it as a global or project model.

The model-discovery protocol is used only to list models and perform basic connection tests. Actual runtime calls use the call endpoint configured for each model. A third-party service that can return a model list does not necessarily implement the complete protocol for images, videos, reference images, asynchronous polling, or structured output. ArcReel does not guarantee that every service claiming compatibility with a protocol will work.

### What should I do if model discovery fails or I see `model_not_found`? {#model-list-failure}

Check the following in order:

1. Use the exact model ID published by the service, not its display name.
2. Confirm that the Base URL, account region, and API Key match.
3. Confirm that the account has access to the model and permission to call the required modality.
4. Confirm that the model is enabled and that its media type and call endpoint are configured correctly.
5. Check whether project-level settings still override the selection with an old model.

Some compatible services do not implement a model-list endpoint. In that case, you can register the model manually. If the actual call still returns 404, ask the service provider to verify routing and permissions.

### Why can generation fail even when the connection test succeeds? {#connection-ok-generation-fails}

The connection test mainly verifies credentials, networking, and model discovery. It does not complete an actual paid image or video generation, so it cannot cover:

- Model generation permissions, balance, and concurrency quotas
- Reference-image, end-frame, resolution, aspect-ratio, and duration limits
- Media types and call endpoints for custom models
- Asynchronous task polling and result downloads

First confirm the provider and model the project actually uses, then inspect the full upstream status code and error message on the failed task.

### What should I do about 401/403, 429, or a network timeout? {#auth-rate-limit-timeout}

- **401/403**: Check whether the API Key is valid, matches the Base URL and region, and has access to the target model.
- **429**: Check the balance, plan quota, RPM, and concurrency limits. Reduce concurrency for the corresponding image, video, or audio channel, then retry after the quota window resets.
- **Network errors or timeouts**: Check DNS, TLS, proxy configuration, and API-domain connectivity from the server running ArcReel. Browser access does not prove that the server container can connect.

A read timeout after a generation request is submitted can leave an uncertain state in which the upstream provider created and billed the task but ArcReel did not receive the response. Do not retry repeatedly without checking. First inspect the provider console for an existing task to avoid duplicate charges.

## Project workflows and tasks {#project-workflow-and-tasks}

### How should I choose a project mode and the Storyboard or Reference-to-video route? Can I change them later? {#choose-mode-and-route}

You must choose two separate dimensions when creating a project:

- **Content Mode**: Narration/Commentary, Drama, or Ad / Short Video. This determines the screenplay structure and production workflow.
- **Video generation route**: Storyboard or Reference-to-video. This determines whether videos use storyboard images or asset reference images.

Content Mode and the video generation route cannot be changed after creation. Multi-grid storyboards are not a third route; they are an image-generation method within the Storyboard route. Ad / Short Video projects do not support multi-grid storyboards. Before creating the full project, use a short sample to confirm that the route fits your needs.

- **Storyboard route**: screenplay → character/scene/prop designs → storyboard images → video. Each shot must have a corresponding storyboard image before video generation.
- **Multi-grid storyboards**: still part of the Storyboard route, as an image-generation method within it. ArcReel first generates one or more grid images, splits them into starting storyboard images for each shot, and then generates video from those storyboard images.
- **Reference-to-video route**: skips storyboard images and directly uses the character, scene, and prop designs referenced by the screenplay as video references.

Reference-to-video does not mean that asset images are unnecessary. If a referenced asset in a Narration/Commentary or Drama project has no design image, its video will fail. In an Ad / Short Video project, a task may continue without merchandise reference images, but merchandise fidelity cannot be guaranteed.

If the video provider receives only text and no storyboard or reference image, verify the project route, actual model, and custom model's call endpoint:

1. The Storyboard route sends the shot's storyboard image as the video's starting image.
2. The Reference-to-video route collects the asset designs referenced by the screenplay.
3. The selected video model and call endpoint must explicitly support the corresponding image-to-video or reference-to-video capability.

If a custom model is registered only with a text-to-video endpoint, or if its declared capabilities do not match the upstream API, ArcReel cannot send reference images to the service using the correct protocol.

### Which source-file formats are supported? {#supported-source-formats}

ArcReel currently supports `.txt`, `.md`, `.docx`, `.epub`, and `.pdf`. Uploaded files are extracted into UTF-8 text.

A scanned PDF with no extractable text cannot be used directly and must go through OCR first. Convert legacy `.doc` files to `.docx` before uploading. If a TXT or Markdown file displays garbled characters, convert it to a common text encoding and upload it again.

### Why is the next stage unavailable, or why did the assistant stop? {#stage-blocked}

Usually a prerequisite review or asset is incomplete:

1. For Narration/Commentary and Drama projects, confirm that the screenplay preprocessing result has been reviewed. Editing it after confirmation requires another confirmation. Ad / Short Video projects do not have this step.
2. Check whether characters, scenes, and props have definitions but no generated design images.
3. On the Storyboard route, check whether the target shot has a storyboard image.
4. On the Reference-to-video route, verify that referenced assets in Narration/Commentary and Drama projects have complete design images. For Ad / Short Video projects, at least confirm that original merchandise images were uploaded. Missing reference images may not block the task, but they reduce merchandise fidelity.
5. Expand the task panel and check for tasks that are still queued, running, or failed.

Do not judge completion solely from the phase number in the header. Assets in the sidebar, episode status, and task errors provide more specific information about what is missing.

### What should I do when a task is queued, running, failed, canceled, or interrupted by a service restart? {#task-states}

Image, video, and audio jobs use independent task channels. You can cancel an individual queued or running task; a running task first enters the “Canceling” state. If you cancel a task with dependencies, the UI warns about downstream tasks that may be affected. Canceling a running task only stops ArcReel-side processing. The upstream task may continue, and any incurred cost is not automatically refunded. Check the provider console when necessary.

The task panel does not have one retry button that works for every task type. After a failure, expand the error details, correct the configuration or input, then regenerate from the corresponding asset, storyboard, or video action. Do not click Generate repeatedly before understanding the cause.

If part of a batch fails, you do not need to redo all successful content. Asset, storyboard, and full-episode video generation fill in missing items; successful results are not deleted because one item failed. After a service restart, safely recoverable video tasks with recorded upstream task IDs resume polling. Unrecoverable tasks are marked failed and wait for the user to decide whether to resubmit them.

To avoid duplicate charges, ArcReel does not unconditionally requeue every running task. Check existing results and tasks in the provider console before regenerating.

## Visuals, video, and audio {#visuals-video-audio}

### How can I improve character consistency? {#character-consistency}

1. Generate and review character designs before generating storyboards and videos in batches.
2. Confirm that the target shot in the screenplay actually references the character.
3. After changing a character design, regenerate the affected storyboards and videos. Existing results are not updated automatically.
4. Validate the selected model's reference-image capabilities with a few shots before scaling up the batch.

When generating a storyboard, ArcReel uses the character, scene, and prop designs referenced by that shot as reference images. Videos on the Storyboard route then use the storyboard image as their first frame, while the Reference-to-video route directly uses asset images. Generation models still cannot guarantee absolute frame-by-frame consistency.

### How can I make adjacent shots more continuous? {#shot-continuity}

On the Storyboard route, when a shot is not the first shot, is not marked as a new segment, and the previous storyboard has already been generated, ArcReel uses that previous storyboard as a continuity reference. This helps carry over composition, color, and setting, but it cannot guarantee a seamless join between two independently generated videos.

Lock down assets and storyboards before generating video, and keep location, time, clothing, and character descriptions consistent across adjacent shots. For important transitions, use a model that supports end frames or handle them in a post-production tool such as Jianying.

### How do start and end frames work? {#first-and-last-frame}

On the Storyboard route, the storyboard image is the video's first frame. You can also select or upload an end frame for each shot, but generation is available only when the current video model explicitly supports end frames. For unsupported models, ArcReel rejects the request instead of silently ignoring the end frame.

The Reference-to-video route has no separate end-frame setting. Changing a storyboard or end frame does not automatically update an existing video; regenerate the corresponding video to apply the change.

### Why are people appearing in a scene design? {#people-in-scene-images}

The prompt for a scene design asks the image model not to include people, but the model may not follow the constraint perfectly. Regenerate the scene image or use instruction-based image editing to remove the people.

A scene design is different from a story-specific storyboard image: the scene design should focus on the environment, while the storyboard image includes people according to the story.

### Does ArcReel support voice-over? {#voice-over-support}

The Web UI currently offers standalone voice-over TTS only for Narration/Commentary. You can preview or generate voice-over segment by segment or for a full episode, then include it in a Jianying draft export. Configure the speech provider, voice, and speed globally or per project; some models do not support speed control.

Built-in speech from a video model, character reference audio, and standalone voice-over TTS are separate capabilities. Character reference audio is currently used on the Reference-to-video route, and the selected video model must explicitly support reference audio. On the Storyboard route, or when only a voice description is available, voice is a soft constraint and cannot guarantee identical timbre across segments.

## Costs, data, and export {#cost-data-export}

### Where can I view costs and Tokens? How can I control spending? {#view-and-control-cost}

The project cost panel displays image, video, text, and audio calls, along with estimated and actual costs. Settings provides call statistics by time range and provider.

Estimates are based on the current model's declared prices and the project structure; they are not the provider's final bill. Costs may be incomplete or display as 0 when a custom provider has no configured prices, a provider returns no usage, or pricing is not registered. Review different currencies separately instead of adding them together. The provider bill is the source of truth for the final amount.

Validate the model, prompts, and reference-image results with a few shots before batch generation. Review each stage before proceeding to prevent an asset error from causing widespread rework. After a generation timeout, check the provider console for an existing task before retrying repeatedly.

There is no fixed “price per minute” that applies to every project. Evaluate costs using the project estimate, actual call details, and provider bill.

### What is the difference between a project ZIP and a full-instance backup? {#project-zip-vs-full-backup}

A project ZIP imports or migrates a single project and can include only the current version or the complete version history. It does not include global provider settings, login configuration, task and cost records, or Agent sessions.

For disaster recovery, use the full-instance backup approach described above and save the project directory, database, and required credentials together.

### Why is my Jianying draft missing or missing clips? {#jianying-draft-issues}

When exporting, select the 5.x or 6+ format that matches your local Jianying version, extract the ZIP directly into the Jianying drafts directory, and restart Jianying. A draft contains only successfully generated video clips. Generate any missing clips in ArcReel before exporting again.

Narration/Commentary currently exports the original novel text as subtitles, Ad / Short Video exports spoken promotional copy as subtitles, and Drama exports dialogue and voice-over subtitles. Narration/Commentary also includes generated voice-over tracks. See the [Jianying draft export guide](./jianying-export.md) for the complete steps.

### Is there a mobile app? Which platforms are supported? {#mobile-app-support}

ArcReel is a self-hosted web application and does not currently have a standalone native iOS or Android app. You can try accessing it from a mobile browser, but a complete mobile editing experience is not guaranteed.

Linux, macOS, or WSL2 is recommended for local use. Native Windows guarantees only project creation and basic workflows.

### Can I use ArcReel commercially or build derivative software? {#commercial-use}

ArcReel is released under [AGPL-3.0](https://github.com/ArcReel/ArcReel/blob/main/LICENSE) with the attribution and modification-notice requirements in [NOTICE](https://github.com/ArcReel/ArcReel/blob/main/NOTICE). The ArcReel name and Logo are not licensed under AGPL-3.0.

To use ArcReel commercially without taking on AGPL open-source obligations, contact [support@arc-reel.com](mailto:support@arc-reel.com) about a commercial license. Users must evaluate compliance for their specific distribution and deployment model. This information is not legal advice.

## Troubleshooting and support {#diagnostics-and-support}

### The UI only shows “Internal Server Error.” How do I find the real cause? {#internal-server-error}

A generic frontend error cannot identify the root cause. First expand the task error, then inspect the terminal running `./scripts/dev.sh` and the status code returned by the provider. You can also download diagnostic logs from the About section of Settings. The diagnostic bundle attempts to mask known credentials in the system summary, but it does not rescan and redact the full contents of existing logs at download time. Before sharing it, manually inspect the entire bundle and remove API keys, Tokens, passwords, the complete `.env`, private endpoint addresses, and project content.

### How do I submit an actionable issue? {#how-to-report-issue}

When filing a [GitHub Issue](https://github.com/ArcReel/ArcReel/issues) or asking the community for help, include:

- The ArcReel version
- The operating system and whether you run natively or through WSL
- The exact failing steps and reproducible actions
- The selected provider, model, and task type
- The error time, a short error keyword, upstream status code, and task ID
- Relevant logs that you have manually redacted

Do not disclose API keys, Tokens, passwords, the complete `.env`, or an unreviewed diagnostic bundle. A screenshot and “How do I fix this?” are usually not enough to diagnose the issue.

## Best practices for quality and cost {#quality-and-cost-best-practices}

Questions such as “Which model is best?”, “How can I achieve 100% character consistency?”, and “How can I make videos perfectly seamless?” have no fixed answer that applies to every project. Use a small, verifiable workflow:

1. Test the target model's visual style, reference-image behavior, duration, and moderation limits with a few shots.
2. Finalize character, scene, and prop designs before generating storyboards in batches.
3. Keep location, time, clothing, and character descriptions consistent across adjacent shots.
4. Reference only the assets that the current shot actually needs so that too many reference images do not dilute the constraints.
5. Use a model that supports end frames for important transitions, or handle them in post-production.
6. Plan the budget using the project estimate, then verify it against actual call details and the provider bill.
