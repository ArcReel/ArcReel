---
id: providers
title: Provider and Model Configuration
sidebar_position: 3
---

# Provider and Model Configuration {#providers}

ArcReel treats the Agent, text generation, image generation, video generation, and TTS as separate capabilities. You can choose different providers for different projects and media types instead of tying the entire production pipeline to one platform.

## 1. Start by Distinguishing Two Provider Categories {#two-provider-categories}

### 1.1 Agent Providers {#assistant-providers}

Under Settings → ArcReel Agent, add and activate an Agent provider credential for project chat, content analysis, script planning, and task orchestration. The Agent uses the Claude Agent SDK and can connect to Anthropic or a compatible gateway.

### 1.2 Media and Text Providers {#media-and-text-providers}

Under Settings → Providers, configure credentials for text, images, video, and text-to-speech (TTS). Then choose the model for each use under Default Models. Generation models use their own provider settings, independently of the Agent provider.

## 2. Find Available Providers and Models {#capability-matrix}

Open a built-in provider under Settings → Providers to find its text, image, video, or audio models. The list shows the models available to this ArcReel version, with price summaries and availability badges. To connect a private gateway, local model server, or a protocol absent from the built-in provider, see [Custom Providers](#custom-providers).

Ark Agent Plan uses the subscription allowance first. ArcReel does not estimate call costs by default. If you enable over-quota postpaid billing for a supported model in the Ark console, Ark bills calls beyond that allowance separately. Check your Ark bill and the [over-quota postpaid billing documentation](https://docs.volcengine.com/docs/ark/agent-plan-personal-over-quota-postpaid-management?lang=zh).

## 3. Configuration Layers {#configuration-layers}

ArcReel selects generation models by use. The project setting takes precedence, followed by the choice under Settings → Default Models. When no default is configured, a compatible custom provider model marked as default can be used as a fallback. If no model is available, configure a provider and fill in the missing settings.

The Recommended badge identifies a built-in provider's suggested model. When you configure the first credential for a built-in provider, ArcReel fills any unset global defaults with its recommended models. Existing global defaults change when you update them. Default Models can prompt you to switch or keep your choice when a recommendation changes.

ArcReel preserves the models in use before an upgrade when it can reconstruct the choices. If an old choice cannot be recovered, select a model again. See [Project Schema Migrations](../ops/deployment.md#project-schema-migrations).

### 3.1 Enabling and Disabling Models {#enable-models}

The detail page of a Preset Provider lists all of its models grouped into text, image, video, and audio. Each row shows the model name,
model ID, and a price summary; models that are retiring, discontinued, or whose calls will be recorded as "Unpriced", are flagged,
and the retiring flag shows the shutdown date. From its shutdown date (a UTC date) on, a model counts as discontinued: it stays in the list,
but it no longer appears among any model candidates, even if you enabled it by hand, and it no longer serves as the provider's default model;
calls from places that still select it fail with an error that gives the shutdown date and the officially recommended successor.
Video tasks already submitted to the vendor before the shutdown date are not affected: when the service restarts and resumes them, their results are retrieved as usual. Tasks submitted after the shutdown date still fail, including tasks queued before it that only reach submission afterwards.
Use "All / Enabled" to switch what is shown.

Models that need a newer version of ArcReel are listed here too, at the end of their group, labelled "Requires ArcReel ≥ version". The current version cannot call them: the toggle is unavailable and the model settings cannot be opened, and they do not appear among any model candidates or serve as the provider's default model. After you upgrade to the required version, they can be enabled like any other model.

In global and project settings, when a default model, a per-purpose model, or the narration model is set to a retiring or discontinued model,
a notice below the dropdown gives the shutdown date and the officially recommended successor.
When the successor is on the same provider and can be used in that place, the notice has a "Switch to …" button: it changes only that one selection,
which takes effect once you save. When the successor is on another provider, disabled, or not usable in that place, only its name is shown.
ArcReel never switches models for you.
A notice also appears when the selected model is no longer in the provider's model list: if ArcReel has received its discontinuation details, the notice says it is discontinued and names the officially recommended successor (if any); otherwise it says the model is no longer available and needs to be reselected.

- Turning off a model's switch disables it immediately: disabled models no longer appear among the global and project default model
  candidates, and when a provider's default model is disabled, automatic selection skips it;
- A model already selected in settings or a project keeps working after it is disabled; to replace it, pick another one in the default model settings;
- The old Agnes models that were hidden in earlier versions appear as disabled after upgrading, and can be re-enabled if needed.

The price summary shows the unit price range for each billing item; for models priced by tiers such as resolution or duration, it shows
the lowest to the highest tier. Actual costs are further multiplied by the provider's multiplier, which is set higher up on the same page.

### 3.2 Model Settings {#model-settings}

Select a model in the provider details to open Provider settings and Model info. Both sections use the same save bar.

| What you need to change | Where to change it |
|---|---|
| A gateway only allows certain durations, reference images, or resolutions | Change Parameters this provider supports. Each change replaces the full item. You can restore the original value. |
| The catalog lists an incorrect capability or price | Edit Model info. The change applies to every provider using the entry. |
| A gateway renamed a model and ArcReel found no matching entry | Choose Link to another model under Model info. You can enter model information if there is no matching entry. |
| Change the protocol, request path, or price multiplier | Edit the call endpoint, request URL, or multiplier under Provider settings. Editable fields depend on the provider and endpoint. |

**Check the effective capabilities**

The interface shows the parameters in effect. This usually means the range supported by both the catalog and the call endpoint, with the smaller limit for any count. Unlisted means ArcReel has no data for that limit. An image model with a reference image limit of zero cannot be used for image-to-image. For ComfyUI, [workflow node bindings](./comfyui.md#bindings) determine the capabilities.

You can enable or disable officially listed provider models. Official data maintains their provider parameters. For a custom provider model or a model you added to a built-in provider, you can change the endpoint and supported parameters. Switching endpoints clears parameter and request path changes for the old endpoint. Save and then check the new endpoint. When you change a custom provider model ID, ArcReel updates saved global and project references. Check the existing selections after changing the ID of a manually added model under a built-in provider.

**Maintain shared model information**

You can edit the display name, capabilities, durations, resolutions, maximum output length, and prices. Set prices by billing item, currency, and condition. A missing price or condition can show as Unpriced in usage records.

When you save changes to official data, the catalog entry uses your values and official sync does not overwrite them. Use official values one at a time or choose Restore official data for the whole entry. If official data later includes a model you added by hand, choose Use official data to switch. Both actions immediately replace your changes. Check the affected providers before confirming.

A manual link takes precedence over a match by model name. Choose Unlink to return to automatic matching. Links and manually entered information apply when you save.

### 3.3 Adding and Fetching Models {#add-models}

Above the model list are two entries, "Add model" and "Fetch models", for both Preset and custom providers.

"Add model" lets you search model names or type a model ID directly, and filter by media type:

- "Disabled models": shown only for Preset Providers. It lists the officially listed models that are disabled; selecting one enables it;
- "Add by model ID": adds the model ID you typed. Its media type comes from the model catalog when the name maps to a known model;
  otherwise it is inferred from the model name, or taken from the media type filter when one is selected;
- "Model catalog": adds a model picked from the model catalog; its price and model info follow the selected entry. Entries already in the
  list are marked "Added"; when a Preset Provider already lists the model (its API model name may differ from the catalog name),
  selecting it enables that listed model.

After adding, the new model's settings open right away so you can adjust its call endpoint and other settings.

"Fetch models" calls the provider's model-listing interface and lists only the models not yet added; already-added models are shown as a
count. The results are split into two groups:

- "Recognized": models that map to a known model in the model catalog, checked by default;
- "Unrecognized": unchecked by default. A model whose name maps to an entry in the model catalog shows the matching model name on its row
  and is priced as that entry once added; a model that maps to no entry, or to an entry without a price, is flagged on its row as
  recorded as "Unpriced". For each row you can click "Match model" to pick one from the model catalog, and the model is then priced as
  the selected one.

For custom providers each row also has a call endpoint dropdown. Its default is inferred from the model name, or from the media type of the
matching model once you pick one. When fetching fails, the reason is shown, and you can check the base URL and key and try again.

Among Preset Providers, AI Studio, Vertex AI, OpenAI, Grok, DashScope, MiniMax, and Agnes support fetching models, which
requires adding a key first. A model ID returned by the provider counts as "Recognized" only when it exactly matches the API model name of an
officially listed model. A provider listing a model does not mean your account or region can call it; likewise, officially listed models
that are not returned stay in the list as usual.

## 4. Choose a Model and Validate a Sample {#six-selection-dimensions}

1. Check the inputs your workflow needs under Parameters This Provider Supports. Storyboard to Video needs a start frame, and Reference to Video needs reference images.
2. Try the same short shot with your candidate models. Review character or product appearance, motion, audio, and delivery resolution before replacing the project's default model.
3. Compare usage and reference costs for generation and rework in [Usage Records](#cost-tracking). Check subscriptions, overage charges, account availability, and terms with the provider.

## 5. Image Provider Configuration {#image-providers}

Image capabilities are mainly used for:

- Character design;
- Scene and prop design;
- Standardized merchandise reference images;
- Single-shot storyboards;
- Multi-grid storyboards;
- Style analysis and image editing.

When choosing a provider, pay particular attention to:

- Support for multiple reference images;
- Character and merchandise identity preservation;
- Rendering of text and logos;
- Target aspect ratios and resolutions;
- Image editing capabilities;
- Error details returned on failure.

Recommendations:

- For character design images, use compositions with simple backgrounds and the full subject visible whenever possible;
- For merchandise reference images, prefer real source material and multiple viewing angles;
- Ordinary storyboards do not all need to use the most expensive model;
- Lock in the characters and visual style before generating in batches.

## 6. Video Provider Configuration {#video-providers}

Video capabilities may include:

- Text-to-video;
- Image-to-video;
- Reference-to-video;
- Start and end frames;
- Video extension;
- Native audio;
- Fixed seeds;
- Different durations and resolutions.

Before choosing a provider, confirm:

- The input types supported by the current model;
- The reference-image count limit;
- The duration per generation;
- Landscape and portrait support;
- Whether audio is generated;
- Task query and cancellation capabilities;
- Whether failed tasks are billed;
- Content moderation rules.

Do not assume that every video model supports the same parameters. ArcReel provides a unified higher-level workflow, but provider-specific capabilities still differ.

## 7. Text Provider Configuration {#text-providers}

Text capabilities are used for:

- Structured screenplays;
- Prompt generation;
- Multimodal understanding;
- Text calls in cost estimates;
- Custom auxiliary tasks.

Consider:

- The reliability of JSON or structured output;
- Long context;
- Chinese-language performance;
- Visual understanding;
- Speed;
- Token pricing;
- Rate limits.

High-reasoning models are not necessarily suitable for every task. Complex content analysis and routine structured transformations can use different models.

## 8. TTS Configuration {#tts-providers}

ArcReel's voice-over capability can use DashScope Qwen TTS and qwen-audio TTS, MiniMax Speech, Gemini TTS (AI Studio, Vertex), Volcengine Ark Agent Plan TTS, or a compatible TTS interface.

After you choose a TTS model in global settings or project settings, the narration voice field lists the common voices available for that model. Voices are listed only when official docs confirm they apply to that model: custom model names, relay aliases, models the official voice list does not cover, and models on an OpenAI-compatible interface that does not connect directly to the official OpenAI address get no list, and they never borrow the voices of another model on the same call endpoint. Having no list does not mean the model cannot take a voice. For voices outside the list (cloned voices, for example), enter the voice ID from the provider docs; a saved voice is never cleared or rewritten because no list is shown.

DashScope speech has two call endpoints: "Alibaba Model Studio (TTS)" calls Qwen TTS (`qwen3-tts` and similar), and "Alibaba Model Studio (qwen-audio TTS)" calls qwen-audio TTS (the `qwen-audio-3.0-tts` and `qwen-audio-3.1-tts` series). The two endpoints do not share voices, and each qwen-audio model only accepts its own voices; see the DashScope [voice list](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list) for the full set. The qwen-audio endpoint supports narration speed from 0.5 to 2.0; values outside this range are sent as the nearest bound. When you add a model, names containing `qwen-audio` are prefilled with the qwen-audio endpoint and other DashScope speech models with the Qwen TTS endpoint; you can change this in [Model settings](#model-settings).

Volcengine Ark Agent Plan TTS reuses the configured dedicated API Key; no additional AK/SK is required. Requests use the plan's dedicated speech endpoint. If speech is unavailable under the plan, concurrency is exhausted, the voice is not authorized, or the plan quota is used up without overage postpaid billing, the task fails with an error code. ArcReel does not switch to a pay-as-you-go endpoint. If [overage postpaid billing](https://docs.volcengine.com/docs/ark/agent-plan-personal-over-quota-postpaid-management?lang=zh) is enabled in the Ark console, calls made after the monthly quota is used up still succeed and Ark bills the overage as postpaid; check your Ark bill for the actual charges. The voice list contains common Chinese voices from the 2.0 model; enter other 2.0 voice IDs from the official [voice list](https://docs.volcengine.com/docs/6561/1257544). Voices from the 1.0 model are not supported. Narration speed ranges from 0.5 to 2.0; out-of-range values fail before submission without incurring a charge. Usage records use the billed character count returned by the service, falling back to the submitted text length when it is absent.

After configuration, first validate:

- Chinese and English pronunciation;
- Personal names, place names, and proper nouns;
- Numbers and units;
- Speaking speed;
- Emotion;
- Maximum length per segment;
- Output audio format.

Before generating in batches, audition a segment of text that contains representative proper nouns.

## 9. Custom Providers {#custom-providers}

Under Settings → Providers, add a custom provider and enter its base URL, credential, and model discovery protocol. Fetch models or add them by hand, then check the media type, model ID, and call endpoint in [Model Settings](#model-settings). The discovery protocol lists models and checks connectivity. Each model's call endpoint handles generation.

Choose a call endpoint that the provider supports. If a private gateway implements only part of the protocol, use [Request Preview and Connection Test](#custom-endpoint-tests) to check the inputs you need, such as reference images, start or end frames, and reference audio. Connection Test generates real output and may incur a charge.

**Choose or adapt an endpoint**

- **OpenRouter Images**. Select OpenRouter Images for OpenRouter image models. This endpoint supports both text-to-image and image-to-image.
- **Ark**. Choose Seedream (1K) or Seedream (2K) by generation. For Seedance, choose the endpoint for 1.x, 2.0, or 2.5. Confirm the endpoint if the model ID omits the generation, for example with an Ark inference endpoint ID.
- **Gemini Video**. Gemini Omni uses the Interactions API and has a separate endpoint from Veo. For compatible Interactions API services, select Gemini Video (Omni) and override the submission and polling paths as needed. Vertex AI endpoints are reserved for built-in providers.
- **Kling Video**. Choose the endpoint for the model's API version. The new API uses Kling Video (New API, Always With Audio) and requires an API key.
- **ComfyUI**. Import an API-format workflow and select its endpoint for the provider model. See [Connect a ComfyUI Workflow](./comfyui.md). Node bindings determine which inputs are supported.
- **Other APIs**. Look in the [Market](./market.md) for an endpoint first. If the provider uses JSON requests and responses, you can start from an example template to create a declarative image or video definition under Call Endpoints.

**Capabilities and request URLs**

Check the effective capabilities under Parameters Supported by This Provider. To describe a limit for just one provider, edit its supported parameters. If ArcReel cannot find a matching catalog entry, [bind the model or enter model information](#model-settings).

If a gateway uses the official protocol with different paths, edit those requests under Model Settings → Request URL. The edited path is appended directly to the base URL. Use only placeholders already available in that request, such as `{{ model }}` or `{{ task_id }}`. A query string replaces the original query parameters, so include any required parameters you need to keep.

Connection Test and generation use the edited paths. Fetch Models and Connectivity Check keep using the provider's base URL. Switching endpoints resets path changes. If a video request's path changes after submission, resuming after a restart can fail. ArcReel does not submit it a second time.

### 9.1 Three Endpoint Test Modes {#custom-endpoint-tests}

The call endpoint detail page in Settings offers three test modes, ordered from lowest to highest risk:

- **Check a response**: paste a provider response from the submit, progress, or fetch stage to inspect every
  extraction path, status mapping, and error field. This does not contact the provider or incur a charge.
  When the base64 path of an image definition matches, only the number of image bytes is shown, not the
  full string.
- **Preview the request**: render the URL, method, headers, and body from sample parameters so you can compare
  them with the provider documentation. This also does not contact the provider or incur a charge.
- **Test connection**: submit one real generation and track it to a terminal state. A billing badge identifies
  that this mode incurs one API call. Endpoints with required assets need the corresponding first frame, last
  frame, reference images, or reference audio before the test can start.

You can cancel a connection test while it is running. Cancellation stops ArcReel from tracking that test and
removes its local result, but the provider's remote job may continue and charges already incurred remain in the
ledger. A successful video artifact plays directly in the result card, and a successful image artifact is
displayed there. The call number links to the spend ledger.
The server retains terminal results and artifacts for 24 hours; they are unavailable after that period.

### 9.2 Adapt a Custom Call Endpoint with an Agent {#custom-endpoint-agent}

When a video or image provider submits jobs as JSON and then polls JSON results by task ID, you can delegate the
adaptation to an external Agent such as Claude Code. Signature-based authentication, multipart requests sent to
the provider, and routing by asset shape are outside the first version of declarative definitions.

Install the public skill:

```bash
npx skills add ArcReel/skills
```

Select both `setup-arcreel-skills` and `adapt-custom-endpoint` from the installation list. You can also
[view or download the same skill source](https://github.com/ArcReel/ArcReel/tree/main/agent_runtime_profile/.claude/skills/adapt-custom-endpoint).

First use the `setup-arcreel-skills` skill to connect ArcReel MCP. It persists the same connection in the current
workspace so the local `adapt-custom-endpoint` script can reuse it across sessions.

Then ask the Agent to read the provider documentation and use this skill. It follows the sequence “write a
definition → run the shared validator → check responses offline → preview requests → run a connection test →
save.” The definition reference and thin HTTP script are downloaded with the skill; the adaptation flow adds no
MCP or SDK tool. Response checks and request previews do not contact the provider. A connection test performs a real
generation and may incur charges, so the Agent must obtain your explicit approval first. When a definition with
the same author and name exists, the Agent may save a copy directly; overwriting the existing endpoint always
requires your explicit approval.

Pass required assets with `--start-image`, `--end-image`, repeatable `--reference-images`, and repeatable
`--reference-audio-files`. These CLI options use the same multipart fields as the Settings page.

## 10. Multiple API Keys {#multiple-api-keys}

You can configure multiple API Keys for the same provider and select the currently active Key.

When using multiple Keys, comply with the provider's terms. This is suitable for:

- Isolating different projects or clients;
- Separating testing and production;
- Quota management;
- Credential rotation.

It should not be used to circumvent provider rate limits, risk controls, or terms of service.

Recommended key rotation procedure:

1. Add the new Key;
2. Validate it with a small task;
3. Make it active;
4. Monitor the error rate;
5. Delete or deactivate the old Key;
6. Update the secret-management records.

## 11. Object Storage and Provider Asset Libraries {#object-storage-and-asset-library}

Under Settings → Asset Library & Storage, you can link images reviewed by the provider or save new images to your TOS bucket. See [Asset Libraries and Object Storage](./asset-storage.md).

- <span id="upload-to-asset-library"></span>[Upload Images to an Asset Library](./asset-storage.md#upload-to-asset-library)
- <span id="select-from-asset-library"></span>[Select an Existing Image from an Asset Library](./asset-storage.md#select-from-asset-library)
- <span id="asset-library-across-projects"></span>[Save to the Global Asset Library and Apply to Other Projects](./asset-storage.md#asset-library-across-projects)
- <span id="asset-library-in-project-zip"></span>[Project ZIPs and Provider Asset Links](./asset-storage.md#asset-library-in-project-zip)
- <span id="generate-with-asset-library"></span>[Use the Asset Library During Generation](./asset-storage.md#generate-with-asset-library)
- <span id="asset-library-in-agent"></span>[Use the Asset Library in a Conversation](./asset-storage.md#asset-library-in-agent)
- <span id="remote-image-storage"></span>[Save New Images to Object Storage](./asset-storage.md#remote-image-storage)

## 12. Cost Tracking {#cost-tracking}

Open Usage Records to inspect the outcome, usage, and reference cost of calls by project, provider, model, or media type. Failed calls can still incur charges. ArcReel reports currencies separately. Check the provider bill for final charges.

ArcReel obtains unit prices from the Model Catalog and applies available usage and request details, such as tokens, cache usage, character or image counts, attached reference images, resolution, quality, audio, and video duration. A call shows Unpriced if usage or a matching unit price is missing. For images generated through declarative or ComfyUI endpoints, the catalog's default tier can differ from the output resolution.

- **Generation costs** apply the provider multiplier to the catalog price. A model on a custom provider or one you added to a built-in provider can also have a model multiplier. The two multipliers are multiplied together. If the model has no catalog match, [bind a model or enter model information](#model-settings).
- **Multiplier changes** apply at settlement for generation calls. Costs already recorded are kept. The usual default is 1. Ark Agent Plan and ComfyUI default to 0, so ArcReel does not estimate their costs by default. Ark may separately bill over-quota calls. See [Ark Agent Plan](#capability-matrix).
- **Agent costs** use actual token usage, including subagent calls, and appear as one record per model per turn. A session uses the active Agent provider, multiplier, and model binding at its start or resume. For an unpriced tier, select an entry from the Model Catalog in the Agent provider details. Clearing the binding restores matching by the actual model name.

## 13. Troubleshooting {#troubleshooting}

### Connection Test Fails {#connection-test-failed}

Check:

- API Key;
- Base URL;
- Model name;
- Network proxy;
- TLS certificate;
- Account region;
- Account balance or quota.

### Text Works, but Images or Videos Do Not {#text-works-media-fails}

Different media capabilities from the same provider may:

- Use different endpoints;
- Require separate activation;
- Use different model names;
- Have different regional restrictions;
- Require asynchronous task queries.

### Frequent Rate Limits {#frequent-rate-limits}

Reduce:

- RPM;
- The number of concurrent tasks;
- The number of shots in each batch.

Also confirm whether multiple ArcReel instances share the same Key.

### Costs Are Significantly Higher Than Estimated {#cost-higher-than-expected}

Check:

- Whether tasks were submitted more than once;
- Whether multiple retries occurred;
- Whether a more expensive model was selected;
- Whether the actual video duration increased;
- Whether native audio was generated;
- Whether the pricing table needs to be updated;
- Whether the provider changed its billing model.
