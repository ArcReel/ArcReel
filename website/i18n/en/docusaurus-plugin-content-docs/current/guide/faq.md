---
id: faq
title: Frequently Asked Questions
sidebar_position: 9
---

# Frequently Asked Questions {#faq}

This page covers the most common installation, configuration, production, and troubleshooting questions for the current version of ArcReel. If this is your first time using ArcReel, start with the [complete getting-started tutorial](./getting-started.md). The Settings page is the source of truth for current provider and model capabilities.

## What capability boundaries should I understand before using ArcReel? {#capability-boundaries}

- Media generation depends on third-party model services. Generation speed, availability, content policies, and costs are controlled by providers.
- Long-form content still requires human review of episode boundaries, character assets, and key plot decisions. ArcReel is designed to empower creators, not eliminate review entirely.
- Video models differ in their support for reference-image counts, video duration, start and end frames, audio, and regional availability.
- Native Windows can run parts of the basic workflow, but POSIX capabilities such as the Agent sandbox degrade. Prefer Linux, macOS, WSL2, or Docker.
- Production environments should use PostgreSQL, HTTPS, strong passwords, and regular backups. Do not expose an unprotected port `1241` directly to the public Internet.

## Installation, deployment, and updates {#install-deploy-update}

### What is the recommended installation method? Can ArcReel run directly on Windows? {#how-to-install}

For a first installation, follow the [Docker steps in Getting Started](./getting-started.md#deploy-arcreel). On Windows, use WSL2 or Docker Desktop. The Agent sandbox has [limitations on native Windows](../ops/deployment.md#container-permissions-and-sandbox).

### What should I do if ArcReel does not open after Docker starts, or the container keeps restarting? {#docker-wont-start}

From the active Compose directory, check `docker compose ps` and `docker compose logs arcreel`, then follow [Service Fails to Start](../ops/deployment.md#service-wont-start). For `SANDBOX_UNAVAILABLE` or `SANDBOX_BWRAP_BROKEN`, see [Container Permissions and the Agent Sandbox](../ops/deployment.md#container-permissions-and-sandbox).

### What are the default login credentials? What should I do if I forget the password? {#default-login}

The default username is `admin`; you can change it in `.env` with `AUTH_USERNAME`. If `AUTH_PASSWORD` is empty, ArcReel generates a password on first startup and writes it back to `.env` in the current deployment directory. The plaintext password is not printed to the logs.

If you forget the password, update `.env` by setting `AUTH_PASSWORD`. Docker deployments must recreate the container to reload environment variables:

```bash
docker compose up -d --force-recreate
```

For a source deployment, restart the ArcReel service. Do not disable authentication on a public deployment.

### Why didn't my `.env` changes take effect? {#env-changes-not-applied}

After changing `.env` in the Compose directory, run `docker compose up -d` to recreate the affected containers. A restart reuses the environment from container creation. See [Environment Variables](../ops/deployment.md#environment-variables).

### How do I update Docker? Will an update delete my projects? {#docker-update}

Back up first, then follow [Upgrades](../ops/deployment.md#upgrade) to pull the image and start the service. ArcReel migrates the database and project data at startup. To return to the earlier version, restore the pre-upgrade backup. About in Settings can check for updates and open the release page.

### Where are projects and configuration stored? How do I back them up or migrate them? {#where-is-data}

The default Docker data root is `projects/` under the Compose directory. The same directory contains `.env` and `claude_data/`. If you use PostgreSQL, also back up the database. If you store images in object storage, keep the objects in the bucket. See [Persistent Directories](../ops/deployment.md#sqlite-volumes) and [Backup and Restore](../ops/deployment.md#backup-and-restore).

## Initial configuration and the Agent {#setup-and-assistant}

### Which credentials do I need to configure before starting production? {#required-credentials}

Project chat uses an [Agent provider](./providers.md#assistant-providers). Text, image, video, and narration generation use their own [generation model settings](./providers.md#media-and-text-providers). If generation fails, first check which model Project Settings actually selects.

### How do I make the Agent start or resume production? {#start-or-resume-assistant}

Open the Agent on the right side of the project and simply ask it to “start production” or “continue production.” The Agent checks the project's current state, resumes from the first incomplete stage, and waits for confirmation when the screenplay, assets, or storyboards need review.

If the Agent reports that you are not logged in, cannot start, or have no available model, first open Settings and confirm that an Agent credential is saved, its message-call probe succeeds, and it is active. A model-discovery warning does not necessarily block message calls, but you should verify the actual model ID. You do not need to sign in to a Claude web account separately in the Agent panel.

### What should I do about `Failed to start Claude Code`, a startup timeout, or `Control request timeout`? {#assistant-startup-failure}

These messages mean that the Agent process did not start normally or could not initialize in time. They do not indicate an error in the screenplay content. Check the following in order:

1. Confirm that the Agent credential is active and that the model ID exactly matches an ID actually exposed by the service.
2. Confirm that the Docker container or server can reach the Agent API and that its proxy, DNS, and TLS configuration works.
3. Confirm that the Agent sandbox passes its startup checks in Docker, WSL, or the local environment.
4. Download diagnostic logs from the About section of Settings and inspect the specific upstream status code near the time of the error.

A successful connectivity check proves only that a minimal request succeeded. Long sessions may still be affected by quotas, context limits, rate limits, or proxy timeouts.

### After starting from source on Windows, the Agent reports that the event loop cannot create subprocesses. What should I do? {#windows-reload-agent}

When the backend is started with `--reload` on native Windows, uvicorn switches to an event loop that cannot create subprocesses, so the Agent cannot launch Claude Code. The UI says that the backend's event loop can't create subprocesses; earlier versions show only `Failed to start Claude Code:` with nothing after the colon.

Restart the backend without `--reload`:

```bash
uv run uvicorn server.app:app --port 1241
```

You can also run `uv run python -m server.app`. It does not enable reload on Windows and reads the listen address from `LISTEN_HOST` and `LISTEN_PORT`. Without reload, restart the backend manually after changing code.

### A subagent fails with `Refusing to use … as an isolation worktree`. What should I do? {#subagent-isolation-worktree}

When the Agent dispatches a subagent, it can ask for the subagent to run in an isolated worktree. ArcReel has subagents share the project directory with the Agent, and Claude Code first uses git to confirm that this directory can serve as the isolated directory; if it cannot confirm this, it refuses the dispatch. Handle it according to the reason in the error:

- **`git could not be run to resolve it`**: git is not available in the runtime environment. Official images include git starting with v0.37.0; for Docker deployments, update the image as described in [How do I update Docker?](#docker-update). When starting from source, install git and restart the backend.
- **`git resolves its working tree to …`**: the project directory is inside the ArcReel source repository. When starting from source, the default data root `projects/` is inside the source repository. Move the data root outside the source repository:
  1. Stop the backend.
  2. Move the entire data root directory out of the source repository, for example to `~/arcreel-data`. The path in the error is `<data root>/projects/<project name>`; drop the trailing `/projects/<project name>` to get the data root. The data root also holds the default SQLite database, logs, and Vertex credentials, so move it as a whole.
  3. Set `ARCREEL_DATA_DIR` to the absolute path of the new directory. This usually goes in `.env`; if the startup environment (such as the shell or a systemd service configuration) already sets this variable, change it there, otherwise the value in `.env` does not take effect.
  4. Start the backend. Existing Agent sessions are linked to the new path automatically at startup.

## Providers, models, and APIs {#providers-models-api}

### Which providers and models does ArcReel support? {#supported-providers}

The built-in providers, model list, and capability markers currently shown in Settings define the supported scope. Support for a provider does not mean that all of its models, account regions, and input forms are available. Image, video, text, speech, reference-image, end-frame, duration, and resolution capabilities are evaluated for each specific model.

Do not infer capabilities from the model name alone. After adding or changing a model, reconfirm its media type, call endpoint, and account permissions.

### How should I enter the Base URL? {#base-url-format}

Prefer a built-in provider so ArcReel can use its built-in address. For a custom provider, enter the protocol root URL specified in the provider's API documentation. Do not enter a console webpage address or blindly append the same `/v1` or `/v1/messages` suffix to every URL.

If the response is `404 page not found`, usually you should verify:

1. Which discovery protocol and call protocol are currently selected.
2. Whether the Base URL points to the correct region and API root path.
3. Whether the service actually implements the corresponding model-list and generation endpoints.

### How do I connect a custom provider? {#custom-provider-setup}

Follow [Custom Providers](./providers.md#custom-providers) to add the base URL, credential, and model. After fetching the model list, check the media type and call endpoint for the model you will use to generate.

### What should I do if model discovery fails or I see `model_not_found`? {#model-list-failure}

Check the following in order:

1. Use the exact model ID published by the service, not its display name.
2. Confirm that the Base URL, account region, and API Key match.
3. Confirm that the account has access to the model and permission to call the required modality.
4. Confirm that the model is enabled and that its media type and call endpoint are configured correctly.
5. Check whether project-level settings still override the selection with an old model.

Some compatible services do not implement a model-list endpoint. In that case, you can register the model manually. If the actual call still returns 404, ask the service provider to verify routing and permissions.

### Why can generation fail even when the connectivity check succeeds? {#connection-ok-generation-fails}

The connectivity check mainly verifies credentials, networking, and model discovery. It does not complete an actual paid image or video generation, so it cannot cover:

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

### How should I choose the project mode, and between storyboard and reference-to-video? Can I change them after creation? {#choose-mode-and-route}

Choose the content mode and generation route when creating the project. Both are fixed after creation. Use Storyboard to Video to review each starting image, or Reference to Video to send asset sheets and other references directly to the video model. Check whether the selected model accepts your inputs. Enable grids in a supported Storyboard to Video project if needed. See the [mode selection table](./workflows.md#mode-selection-table).

If the provider did not receive the expected images, check the storyboard's starting image or the asset sheets referenced by the video unit, then check the [model's effective capabilities](./providers.md#model-settings).

### Which source-file formats are supported? {#supported-source-formats}

ArcReel currently supports `.txt`, `.md`, `.docx`, `.epub`, and `.pdf`. Uploaded files are extracted into UTF-8 text.

A scanned PDF with no extractable text cannot be used directly and must go through OCR first. Convert legacy `.doc` files to `.docx` before uploading. If a TXT or Markdown file displays garbled characters, convert it to a common text encoding and upload it again.

### Why can't I move to the next stage, or why did the Agent stop? {#stage-blocked}

Start with Production Status in the episode and any failed tasks. Follow the workspace messages to clear prerequisites:

1. In Narration or Drama, confirm the script plan first. Edit an existing formal script on the timeline. To replace it with a new plan, confirm that plan again.
2. Check the assets referenced in the script and their required asset sheets. Storyboard to Video also needs a usable storyboard image.
3. Wait for queued or running tasks. If a task failed, open its details and fix the input or configuration it identifies.

See [Production Stages](./workflows.md#production-stages) for what to review at each step.

### What should I do when a task is queued, running, failed, canceled, or interrupted by a service restart? {#task-states}

Image, video, and audio jobs use independent task channels. Only queued tasks can be canceled. Once a task starts running, it can no longer be canceled: the provider call already sent runs to completion and its result is saved as an artifact, so the cost already incurred is not wasted. If you are not satisfied with the result, switch back to an earlier version in the version history or regenerate. If you cancel a task with dependencies, the UI lists the queued downstream tasks that will be canceled with it.

The task panel does not have one retry button that works for every task type. After a failure, expand the error details, correct the configuration or input, then regenerate from the corresponding asset, storyboard, or video action. Do not click Generate repeatedly before understanding the cause.

There is one exception: when a video was generated successfully on the provider side but could not be downloaded, the task panel offers **Retry download**. This action resumes the original provider task and only re-fetches the output. It **does not resubmit the request and is not billed again**, so use it to recover this kind of failure instead of regenerating from the generation entry point. Regenerating creates a new paid task.

Once the retry starts, Usage Records removes the previous failure entry in each open tab as the task status syncs.

If the workspace reports that the service is temporarily unavailable, wait for it to come back online and select **Retry download** again.

If part of a batch fails, you do not need to redo all successful content. Asset, storyboard, and full-episode video generation fill in missing items; successful results are not deleted because one item failed. After a service restart, safely recoverable video tasks with recorded upstream task IDs resume polling. Unrecoverable tasks are marked failed and wait for the user to decide whether to resubmit them.

To avoid duplicate charges, ArcReel does not unconditionally requeue every running task. Check existing results and tasks in the provider console before regenerating.

## Visuals, video, and audio {#visuals-video-audio}

### How can I improve character consistency? {#character-consistency}

1. Generate and review character asset images before generating storyboards and videos in batches.
2. Confirm that the target shot in the screenplay actually references the character.
3. After changing a character asset image, regenerate the affected storyboards and videos. Existing results are not updated automatically.
4. Validate the selected model's reference-image capabilities with a few shots before scaling up the batch.

When generating a storyboard, ArcReel uses the character, scene, and prop asset images referenced by that shot as reference images. Videos in Storyboard mode then use the storyboard image as their first frame, while Reference-to-video mode directly uses asset images. Generation models still cannot guarantee absolute frame-by-frame consistency.

### How can I make adjacent shots more continuous? {#shot-continuity}

In Storyboard mode, when a shot is not the first shot, is not marked as a new segment, and the previous storyboard has already been generated, ArcReel uses that previous storyboard as a continuity reference. This helps carry over composition, color, and setting, but it cannot guarantee a seamless join between two independently generated videos.

When you submit storyboard images in the web app or through the Agent, ArcReel counts the previous storyboard toward the image model's reference limit if it is available, in the generation queue, or approved for generation in the same batch. Shots marked as chapter breaks skip the previous storyboard. If you exceed the limit, ArcReel asks you to adjust the references, set the shot as a chapter break, or switch models before creating a task.

Lock down assets and storyboards before generating video, and keep location, time, clothing, and character descriptions consistent across adjacent shots. For important transitions, use a model that supports end frames or handle them in a post-production tool such as Jianying.

### How do start and end frames work? {#first-and-last-frame}

In Storyboard mode, the storyboard image is the video's first frame. You can also select or upload an end frame for each shot, but generation is available only when the current video model explicitly supports end frames. For unsupported models, ArcReel rejects the request instead of silently ignoring the end frame. Changing a storyboard or end frame does not automatically update an existing video; regenerate the corresponding video to apply the change.

Reference-to-video mode has no separate end-frame setting.

### Why do people appear in a scene asset image? {#people-in-scene-images}

The prompt for a scene asset image asks the image model not to include people, but the model may not follow the constraint perfectly. Regenerate the scene image or use instruction-based image editing to remove the people.

A scene asset image is different from a story storyboard image: the scene asset image should focus on the environment, while the storyboard image includes people according to the story.

### Does ArcReel support voice-over? {#voice-over-support}

The Web UI currently offers standalone voice-over TTS only for Narration/Commentary. You can preview or generate voice-over segment by segment or for a full episode, then include it in a Jianying draft export. Narration delivery is a project setting. With "TTS narration" the project keeps its own TTS model, voice and speed, prefilled from the global defaults at creation; later changes to the global defaults do not affect existing projects. With "post-production voiceover" ArcReel does not generate narration audio and you add the voiceover during editing. You can switch delivery in the project settings at any time without making existing content outdated. Some models do not support speed control; the speed input is then unavailable.

Built-in speech from the video model, character reference audio, and voice-over TTS are three separate capabilities. By default, character voices are bound by prompt constraint: each character's voice description is written into the prompt and the video model voices it accordingly, so the voice may vary slightly between clips. To have the video model reuse a character's actual voice, set "Character voice binding" to "Reference audio" in project settings and make sure the project uses Reference-to-video mode, the selected video model supports reference audio, and every speaking character has reference audio configured. Storyboard (image-to-video) mode does not support reference audio.

## Costs, data, and export {#cost-data-export}

### Where can I view costs and Tokens? How can I control spending? {#view-and-control-cost}

The project cost panel displays image, video, text, and audio calls, along with estimated and actual costs. Settings provides call statistics by time range and provider.

Estimates are based on the current model's declared prices and the project structure; they are not the provider's final bill. When a model maps to no known model in the model catalog, a provider returns no usage, or the model catalog has no applicable unit price, the call is recorded as "Unpriced" and left out of the totals. Review different currencies separately instead of adding them together. The provider bill is the source of truth for the final amount.

Validate the model, prompts, and reference-image results with a few shots before batch generation. Review each stage before proceeding to prevent an asset error from causing widespread rework. After a generation timeout, check the provider console for an existing task and avoid repeated retries.

There is no fixed “price per minute” that applies to every project. Evaluate costs using the project estimate, actual call details, and provider bill.

### Can I recover content I deleted by mistake? {#recover-deleted-content}

Yes. Every deletion inside a project, whether you or the Agent starts it, becomes a deletion record in the project trash and is kept for 30 days. Open the **Project trash** section under **Files** in the workspace, select a record, and click **Restore**. A restore only adds and does not overwrite existing content. If the original name or number is already taken, the restored object gets a new name or number, and the notification states it.

Deleting a whole project moves it to **Deleted projects** on the project list page. For how each type of object is deleted and restored, restoring after an asset merge, and what the Agent can delete and restore, see [File View and Project Trash](./files-and-trash.md).

### What is the difference between a project ZIP and a full-instance backup? {#project-zip-vs-full-backup}

A project ZIP imports or migrates a single project and can include only the current version or the complete version history. It does not include global provider settings, login configuration, task and cost records, Agent sessions, or Agent memory. Available provider asset links of the images go along with the project ZIP; see [Project ZIPs and Provider Asset Links](./asset-storage.md#asset-library-in-project-zip) for how they are used after import.

A current-version-only ZIP keeps the selected upload record and snapshot for a manually uploaded storyboard image. After import, changes to the storyboard prompt or project style leave the uploaded image current. For an AI-generated storyboard image, the ZIP includes only the current image file.

The project trash is not included in a project ZIP, while a full data-root backup keeps its deletion records. For how to use the project trash, see [File View and Project Trash](./files-and-trash.md).

A deleted project goes to **Deleted projects** on the project list page, together with its project trash, and is kept for 30 days. You can restore it as a whole or delete it permanently; see [Deleted projects](./files-and-trash.md#deleted-projects).

When an imported project has the same name as an existing one, choosing **Overwrite Existing Project** is the same as deleting the existing project and then importing: the existing project's queued tasks are cancelled; its tasks, usage records, and Agent sessions stay with the deleted project instead of carrying over to the imported one; and the existing project, including its Agent memory, goes to **Deleted projects**. To keep both projects, choose **Auto Rename and Import**.

For disaster recovery, use the full-instance backup approach described above and save the project directory, database, and required credentials together.

### Why is my Jianying draft missing or missing clips? {#jianying-draft-issues}

Check the selected edit timeline and Jianying version. After extraction, the timeline's folder should sit directly in your Jianying draft directory. If the draft is still missing or exporting is blocked, see [Jianying export troubleshooting](./jianying-export.md#troubleshooting).

### Is there a mobile app? Which platforms are supported? {#mobile-app-support}

ArcReel is a self-hosted web application and does not currently have a standalone native iOS or Android app. You can try accessing it from a mobile browser, but a complete mobile editing experience is not guaranteed.

Linux, macOS, WSL2, or Docker is recommended for the server. Native Windows guarantees only project creation and basic workflows; use WSL2 or Docker Desktop for a production deployment.

### Can I use ArcReel commercially or build derivative software? {#commercial-use}

ArcReel is released under [AGPL-3.0](https://github.com/ArcReel/ArcReel/blob/main/LICENSE) with the attribution and modification-notice requirements in [NOTICE](https://github.com/ArcReel/ArcReel/blob/main/NOTICE). The ArcReel name and Logo are not licensed under AGPL-3.0.

To use ArcReel commercially without taking on AGPL open-source obligations, contact [support@arc-reel.com](mailto:support@arc-reel.com) about a commercial license. Users must evaluate compliance for their specific distribution and deployment model. This information is not legal advice.

## Troubleshooting and support {#diagnostics-and-support}

### The UI only shows “Internal Server Error.” How do I find the real cause? {#internal-server-error}

A generic frontend error cannot identify the root cause. First expand the task error, then inspect the server logs and the status code returned by the provider. For a Docker deployment, run this command in the Compose directory:

```bash
docker compose logs arcreel
```

You can also download diagnostic logs from the About section of Settings. The diagnostic bundle attempts to mask known credentials in the system summary, but it does not rescan and redact the full contents of existing logs at download time. Before sharing it, manually inspect the entire bundle and remove API keys, Tokens, passwords, the complete `.env`, private endpoint addresses, and project content.

### How do I submit an actionable issue? {#how-to-report-issue}

When filing a [GitHub Issue](https://github.com/ArcReel/ArcReel/issues) or asking the community for help, include:

- The ArcReel version
- The operating system and whether you deployed with Docker, WSL, or from source
- The exact failing steps and reproducible actions
- The selected provider, model, and task type
- The error time, a short error keyword, upstream status code, and task ID
- Relevant logs that you have manually redacted

Do not disclose API keys, Tokens, passwords, the complete `.env`, or an unreviewed diagnostic bundle. A screenshot and “How do I fix this?” are usually not enough to diagnose the issue.

## Best practices for quality and cost {#quality-and-cost-best-practices}

Validate assets, model behavior, and costs on a few shots before you scale up. See [Consistency Practices](./workflows.md#consistency-practices) and [Cost Control](./workflows.md#cost-control-practices).
