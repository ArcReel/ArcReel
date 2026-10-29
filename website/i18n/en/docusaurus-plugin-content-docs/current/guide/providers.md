---
id: providers
title: Provider and Model Configuration
sidebar_position: 3
update_docs: fact-check
---

# Provider and Model Configuration {#providers}

ArcReel treats the Agent, text generation, image generation, video generation, and TTS as separate capabilities. You can choose different providers for different projects and media types instead of tying the entire production pipeline to one platform.

## 1. Start by Distinguishing Two Provider Categories {#two-provider-categories}

### 1.1 Agent Providers {#assistant-providers}

The Agent is responsible for:

- Understanding user intent;
- Analyzing novels and screenplays;
- Identifying characters, scenes, and props while planning each episode's script;
- Planning episodes;
- Normalizing screenplays;
- Orchestrating subsequent generation tasks.

ArcReel's Agent Runtime is based on the Claude Agent SDK and supports Anthropic's official service as well as compatible configurations supported by the project.

The Agent provider does not necessarily handle the actual image and video generation.

### 1.2 Media and Text Providers {#media-and-text-providers}

These providers are responsible for:

- Structured text generation;
- Image generation and editing;
- Video generation;
- TTS.

A complete project can use a combination of providers, for example:

- Agent: a high-quality reasoning model;
- Text structuring: a faster text model;
- Character design: a high-quality image model;
- Batch storyboards: a low-cost image model;
- Sample videos: a fast video model;
- Final key shots: a high-quality video model;
- Voice-over: a dedicated TTS provider.

## 2. Preset Provider Capability Matrix {#capability-matrix}

Providers' specific models, parameters, regional availability, and pricing change continually. The following table only shows the capabilities covered by ArcReel; the actual available options are shown on the Settings page.

| Provider | Text | Image | Video | TTS | Typical Uses |
|---|:---:|:---:|:---:|:---:|---|
| Gemini | ✅ | ✅ | ✅ | ✅ | Multimodal text, reference images, video generation, and TTS |
| Volcengine Ark | ✅ | ✅ | ✅ | — | Text, image, and video generation in mainland China network environments |
| Grok | ✅ | ✅ | ✅ | — | Text, image, and video generation |
| OpenAI | ✅ | ✅ | — | — | Text and image generation |
| Vidu | — | ✅ | ✅ | — | Image, image-to-video, and reference-to-video generation |
| DashScope | ✅ | ✅ | ✅ | ✅ | Text, image, video, and voice-over TTS |
| MiniMax | ✅ | ✅ | ✅ | ✅ | Text, image, video generation, and TTS |
| Kling | — | ✅ | ✅ | — | Image, image-to-video, and reference-to-video generation |
| Agnes | ✅ | ✅ | ✅ | — | Text, image, and video generation |
| Custom Providers | Interface-dependent | Interface-dependent | Interface-dependent | Interface-dependent | Private gateways, local models, or third-party compatible services |

The built-in OpenAI provider offers text and image capabilities. OpenAI-compatible video and TTS are connected through a Custom Provider's call endpoints.

## 3. Configuration Layers {#configuration-layers}

ArcReel supports:

1. **Global default providers**;
2. **Project-level provider overrides**;
3. **Multiple API Keys for a Preset Provider** (each Custom Provider configuration uses one API Key);
4. **Different providers for different media types**.

The priority is generally:

```text
Project-level configuration > Global default configuration
```

Project-level overrides are suitable when:

- A project needs a specific visual style;
- A client requires a specific provider;
- Project budgets vary significantly;
- You need to compare results from two providers;
- Only some services are accessible in a particular region.

### 3.1 Enabling and Disabling Models {#enable-models}

The detail page of a Preset Provider lists all of its models grouped into text, image, video, and audio. Each row shows the model name,
model ID, and a price summary; models that are discontinued, or whose calls will be recorded as "Unpriced", are flagged.
Use "All / Enabled" to switch what is shown.

- Turning off a model's switch disables it immediately: disabled models no longer appear among the global and project default model
  candidates, and when a provider's default model is disabled, automatic selection skips it;
- A model already selected in settings or a project keeps working after it is disabled; to replace it, pick another one in the default model settings;
- The old Agnes models that were hidden in earlier versions appear as disabled after upgrading, and can be re-enabled if needed.

The price summary shows the unit price range for each billing item; for models priced by tiers such as resolution or duration, it shows
the lowest to the highest tier. Actual costs are further multiplied by the provider's multiplier, which is set higher up on the same page.

### 3.2 Model Settings {#model-settings}

Click a row in the model list to open that model's settings on the right; this works the same for Preset and custom providers. The upper
part, "Provider settings", applies only to the current provider. The lower part, "Model info", is the model's record in the model catalog
and is shared by every provider that uses the model. Both parts share the save bar at the bottom and are saved together, and closing with
unsaved changes asks first.

- Officially listed built-in models can only be enabled or disabled; their other settings are maintained officially;
- Models of custom providers, and models you added yourself under a built-in provider, also have:
  - "Model ID": the model name sent to the provider with each call. After changing it, settings and projects that selected this model need to select it again;
  - "Multiplier": this model's multiplier. When the provider multiplier is not 1, the final multiplier of the two is shown;
  - "Parameters this provider supports": change these when what the provider actually supports differs from the model info, for example
    when a relay only offers some durations or resolutions, or does not support the last frame. Each item shows its original value;
    "Change" replaces the whole item and "Restore" goes back to the original value. Models that map to no known model can be changed too;
    their original values are what ArcReel infers from the model name. For video models, "Durations by resolution" follows "Durations",
    and removing a resolution also removes its duration restriction;
  - "Remove from 'provider name'": removal asks for confirmation first. When settings or projects use the model, the dialog lists them,
    and after removal you need to select another model for them;
- Models of custom providers also have "Set as default", "Call endpoint", "Request URL", and a default resolution. Changing the endpoint
  resets modified request URLs to their defaults and clears the changes under "Parameters this provider supports"; save first, then adjust
  them for the new endpoint.

In "Model info" you can change the display name, capabilities, durations, durations by resolution, resolutions, and the maximum output
length; the model maker, official model name, type, and aliases are read-only. A line at the top says who maintains the information:

- Maintained officially: it is updated with official data. Before you save a change, a note above the save bar says that after saving,
  this model uses your information and no longer follows official updates;
- Modified by you: official updates do not overwrite your changes, and the row is marked "Modified" in the model list. Each changed field
  shows the official value below it, and "Use" takes that one value back. "Restore official data", after confirmation, discards all your
  changes and returns to official data. It takes effect immediately without the save bar and cannot be undone;
- No official information, filled in by you: the model catalog had no information for this model, and it does not follow official
  updates. If the official data later adds the same model, "Use official data" appears here: after confirmation, the information you
  entered is replaced by the official data. It takes effect immediately and cannot be undone.

When a relay renames a model so it no longer matches the model catalog by name, models you added yourself and models of custom providers
can be linked to a catalog model by hand. "Link to another model" at the top right of the lower half opens the model catalog picker
(it lists models of the same type by default); after you choose one, capabilities, price, and call-channel capabilities all come from
that model. A linked model is no longer matched by name; click "Unlink" to go back. For a model that is not in the catalog, click
"Fill in manually" to enter the model maker, model name, display name, type, and capabilities; the durations of a video model are
prefilled from the model name. The price is optional, and calls are recorded as unpriced without it. If the catalog already has a model
with the same name, you cannot fill it in; select that model instead. All of this only changes the draft, takes effect with the save bar
at the bottom, and leaves nothing behind if you cancel.

The "Pricing" part of "Model info" is edited as one card per billing item, and you can use it to add prices the catalog does not have:

- Choose the currency first (US dollar or Chinese yuan). A price table uses the currency of the model maker's main site, with no
  exchange-rate conversion;
- "Add billing item" lists only the items available for this type of model, such as "Input", "Cache hit", and "Output" for text, or
  "Video duration" for video. An item without conditions has one price line. After "Price by condition" adds a condition such as
  resolution, audio, or service tier (Standard, Flex, Fast), each tier gets a "condition → price" line; click a condition tag to change
  its value, and "Add tier" adds a line. A condition cannot be removed once added: to change conditions, delete the whole card and add
  the item again;
- When a request gives no value for a condition, the line below the card, "When a request gives no condition, use", supplies it. A
  condition without a value cannot be priced, so the call is recorded as unpriced;
- Items such as reference images can have a free allowance: only the part of a request beyond it is charged;
- Without a price, call costs are recorded as unpriced. When the entry has been modified, the official pricing is shown below the
  prices and "Use the official pricing" takes it back; it is not shown when the currency differs from the official one.

When other providers use the same model, "Model info" lists them: changes and restoring official data apply to them as well. If only one
provider differs from the model info (for example, a relay offers only some durations), change "Parameters this provider supports"
instead of the model info.

The name, base URL, key, concurrency limits, and provider multiplier of a custom provider are still saved together in the provider details,
and saving them does not change the models in the model list.

In the details of a usage record, click the model name to open that model's settings; when the provider has been deleted or is an Agent credential, only the model name is shown.

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

## 4. Six Dimensions for Choosing a Provider {#six-selection-dimensions}

Do not consider only the result of a single generation.

### 4.1 Quality {#dimension-quality}

Consider:

- Character identity preservation;
- Merchandise structure preservation;
- Image composition;
- Naturalness of motion;
- Prompt adherence;
- Long-take stability;
- Output resolution.

### 4.2 Controllability {#dimension-controllability}

Check whether the provider supports:

- Multiple reference images;
- Start and end frames;
- Negative prompts;
- Fixed seeds;
- Video extension;
- Reference-to-video generation;
- Native audio;
- Structured output.

### 4.3 Reliability {#dimension-reliability}

Consider:

- API success rate;
- Queue time;
- Rate-limiting behavior;
- Timeouts;
- Task queries;
- Idempotency;
- Whether failed requests can be retried safely.

### 4.4 Cost {#dimension-cost}

Compare providers against the same complete shot objective, not just their unit prices.

Total cost may include:

- Text Tokens;
- Character, scene, and prop images;
- Storyboard images;
- Video seconds;
- Retries after failures;
- TTS;
- Rework caused by quality issues.

### 4.5 Speed {#dimension-speed}

Distinguish between:

- API response time;
- Queue time;
- Actual generation time;
- Fluctuations during peak periods;
- Concurrency capacity for batch tasks.

### 4.6 Compliance and Regional Availability {#dimension-compliance-and-region}

Confirm:

- The region in which the account is registered;
- Whether the API is available in the deployment region;
- Content policies;
- Data processing requirements;
- Commercial use terms;
- Requirements for labeling generated content.

## 5. Recommended Tiered Strategies {#tiered-strategies}

### 5.1 Budget Validation {#strategy-budget}

Use for:

- Trial runs for new projects;
- Prompt validation;
- Confirming the number of storyboards;
- Testing the direction of shot motion.

Strategy:

- Use a small amount of content;
- Use a fast text model;
- Use a low-cost storyboard model;
- Use a fast video tier;
- Strictly limit batch sizes.

### 5.2 Balanced Production {#strategy-balanced}

Use for:

- Routine batch content;
- Validated characters and visual styles;
- Workloads that require stable throughput.

Strategy:

- Use high-quality models for key assets;
- Use balanced models for ordinary storyboards;
- Tier video models by shot importance;
- Reserve budget for retries after failures.

### 5.3 Premium Delivery {#strategy-premium}

Use for:

- Key plot moments;
- Cover shots;
- Merchandise close-ups;
- Branded deliverables;
- Final videos that need higher resolution.

Strategy:

- Use high-quality reference assets;
- Review shots more rigorously;
- Create a low-cost preview first;
- Use expensive models only for approved shots;
- Retain manual post-production.

## 6. Image Provider Configuration {#image-providers}

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

## 7. Video Provider Configuration {#video-providers}

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

## 8. Text Provider Configuration {#text-providers}

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

## 9. TTS Configuration {#tts-providers}

ArcReel's voice-over capability can use DashScope Qwen TTS, MiniMax Speech, Gemini TTS (AI Studio, Vertex), or a compatible TTS interface.

After configuration, first validate:

- Chinese and English pronunciation;
- Personal names, place names, and proper nouns;
- Numbers and units;
- Speaking speed;
- Emotion;
- Maximum length per segment;
- Output audio format.

Before generating in batches, audition a segment of text that contains representative proper nouns.

## 10. Custom Providers {#custom-providers}

ArcReel can connect to OpenAI-compatible or Google-compatible services. Typical uses include:

- Self-hosted API gateways;
- Centralized enterprise secret management;
- Private model services;
- Services built around a local Ollama or vLLM deployment;
- Third-party compatible platforms.

A typical configuration includes:

- Name;
- Base URL;
- API Key;
- Model name;
- Media type;
- Optional provider-specific parameters.

The model discovery protocol only determines which type of model-listing interface ArcReel uses. How each model is actually called is determined by its bound call endpoint. ArcReel can infer the media type and call endpoint from the model name, but compatibility with a protocol does not mean that every capability and parameter is available. You can adjust them in the [model settings](#model-settings) after adding models, and you should validate each of the following:

1. Text connectivity;
2. Structured output;
3. Image generation;
4. Video task creation;
5. Video task queries;
6. TTS;
7. Error codes and rate-limiting behavior.

Model names are mapped to known models in the model catalog, ignoring case, ignoring any namespace before the last `/`, and treating dots as hyphens. For a mapped model, selectable durations, resolutions, maximum output length, and price all come from the model catalog; an unmapped model still works, its video durations are inferred from the model name, and its cost is recorded as "Unpriced".

Each video call endpoint decides first/last frame, reference image, and reference audio support from the model name. When the name maps to a known model in the model catalog, the endpoint uses that model's official name and aliases. A video model the endpoint does not recognize supports only text-to-video and first-frame generation; last frame, reference images, and reference audio are unavailable. For image models, text-to-image and image-to-image are available only where both the model catalog entry and the call endpoint support them; a model that maps to no known model follows the call endpoint alone.

To use image models through OpenRouter, change the call endpoint to "OpenRouter Images API" in [model settings](#model-settings). OpenRouter does not provide OpenAI's `/images/edits` endpoint, so models attached to "OpenAI Images API" cannot do image-to-image. With "OpenRouter Images API", both text-to-image and image-to-image go through `/images`, and the aspect ratio, resolution, and reference image limit follow the parameters OpenRouter declares for that model. When there are more reference images than the model allows, only the first ones are sent and the task result shows a notice; if any reference image cannot be read, generation fails right away instead of continuing with fewer reference images.

To use Volcengine Ark Seedream through a custom provider, use the "Volcengine Ark (Seedream)" call endpoint. Models whose names contain `seedream` are inferred to this endpoint by default. Ark does not provide an `/images/edits` endpoint, so both text-to-image and generation with reference images go through `/images/generations`, with up to 14 reference images sent in the request. The size follows Ark's recommended width and height for the project aspect ratio, and billing matches the preset Volcengine Ark provider. Seedream models added earlier that are still attached to "OpenAI Images API" keep working for text-to-image; generation with reference images fails with a message asking you to change the call endpoint. Change it to "Volcengine Ark (Seedream)" in [model settings](#model-settings).

If someone has already adapted the call endpoint you need, you can install it directly from the [Market](./market.md) instead of writing the definition yourself.

When a provider submits jobs as JSON and then polls JSON results by task ID, you can click "New endpoint" in the "Endpoints" section and write a declarative definition yourself.

Declarative definitions work for both video and image providers: a definition with `"media_type": "image"` is an image definition, and a definition without it is a video definition. After you attach an image definition to an image model of a custom provider, it can generate storyboard images and asset images. An image definition that declares image-to-image can also edit images.

When creating an endpoint, you can pick the video or image "submit + poll" template under "Example template" as a starting point. Choosing a template replaces the current draft.

For a self-hosted ComfyUI instance, you can import an API-format workflow and map prompts, assets, dimensions, duration, and output to its nodes. See [Connect a ComfyUI Workflow](./comfyui.md) for the complete setup and operational constraints.

When a relay keeps an official protocol but changes the path (an added prefix, a different version segment, or a renamed path), you can change the "Request URL" in the [model settings](#model-settings). Each request the endpoint sends gets its own row; the base URL part is greyed out, and you click the path to edit it. Press Enter or click elsewhere to save, or press Esc to cancel. A changed path is appended to the base URL exactly as the provider entered it, without adding `/v1` or stripping a version segment. To drop only what was added or stripped automatically, open the path and press Enter without changing it. The path can only use placeholders the request already has, such as `{{ model }}` and `{{ task_id }}`. If the path includes a `?` query string, it replaces the original query string entirely; add any original parameters you need to keep (such as `alt=media` in the Gemini download URL) to the changed path.

Connection tests and real generation use the changed URLs; model discovery and connectivity checks still use the base URL. Changing the endpoint resets modified request URLs to their defaults. If you change a request URL after a video task is submitted, resuming that task after a service restart fails instead of submitting it again. Endpoints without a "Request URL" row in the model settings do not support this.

### 10.1 Three Endpoint Test Modes {#custom-endpoint-tests}

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

### 10.2 Adapt a Custom Call Endpoint with an Agent {#custom-endpoint-agent}

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

## 11. Multiple API Keys {#multiple-api-keys}

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

## 12. Cost Tracking {#cost-tracking}

ArcReel can record costs by provider and media type, but keep in mind:

- Different providers use different billing units;
- Different currencies should not be combined into a single unexplained total;
- Providers may change their pricing;
- Some failed tasks may still incur charges;
- The provider's final invoice is authoritative.

Costs of text, voiceover, image, and video calls on built-in providers are computed from each model's official prices:

- Cache hits and cache writes are charged at their own prices, not as regular input;
- For models priced by input length, the tier is chosen by the total input of the single request (including cache), and the whole request uses that tier's prices;
- Voiceover models that officially count each Chinese character as 2 characters are counted the same way;
- Images are priced by the resolution and quality actually sent to the provider; when the request carries no resolution, the provider's default tier applies;
- For image models that officially charge for reference images (for example, beyond a number of free images), reference images are counted per image;
- Videos are priced by the seconds actually generated and by the resolution, audio, and duration tier sent to the provider; Vidu reference-to-video uses its own prices;
- Ark Seedance is billed by video tokens: settlement uses the token count the provider reports, and pre-submit estimates convert with the official formula (width × height × frame rate × seconds / 1024). When the aspect ratio is left to the provider (adaptive), the token count is unknown and the estimate is recorded as "Unpriced";
- A call with no applicable price is recorded as "Unpriced" instead of being estimated with another model's or another tier's price.

Models on a custom provider have no prices of their own. When a model name maps to a known model in the model catalog, its calls are charged at that model's official price, with the same rules as built-in providers;
when it maps to nothing, its calls are recorded as "Unpriced". The model list on the settings page shows the known model each model maps to, or "Unpriced".

Each provider can have a multiplier that applies to the cost of all its calls, to reflect top-up discounts or relay price differences.
Models of custom providers, and models you added yourself under a built-in provider, can also have their own multiplier, so the final cost = official price × provider multiplier × model multiplier. For example, if a relay charges 80% of the official price across the board, set the provider multiplier to 0.8; if one model there costs 1.5× the official price, set that model's multiplier to 1.875 (0.8 × 1.875 = 1.5).
The multiplier defaults to 1; Volcengine Ark Agent Plan is billed by subscription and ComfyUI providers run on your own hardware, so their multipliers default to 0 and no cost is computed. Changing a multiplier only affects later calls;
costs already recorded are not recalculated.

Agent session costs are computed from the same model catalog; the cost reported by the Claude Agent SDK is not used:

- Each model request is priced by its own token usage and total input, and subagent requests are included.
- Each model used in a conversation turn gets one record, named after the model that was actually called.
- When a model name does not match any known model in the catalog, its tokens are still recorded and the cost is marked "Unpriced".
- Calls that do not appear in the message stream, such as context compaction, are filled in from the cumulative usage the SDK reports. The first turn after a session is resumed has no baseline to compare against, so such calls are not counted in that turn.

Each Agent provider can have a multiplier, applied to the cost of every Agent call made through that provider. It defaults to 1. Gateways billed by subscription can set the multiplier to 0 so that no cost is computed. A session is billed with the Agent provider that was active when the session started (or was resumed), using that provider's multiplier and manually selected models (described below) as they were at that moment. Switching the active provider, changing the multiplier, or changing a selected model mid-session does not affect sessions already in progress; sessions started (or resumed) afterwards use the new settings. Recorded costs are not recalculated.

The Agent provider list in Settings shows the model actually called by the main model and by each of the four tiers (Haiku, Sonnet, Opus, and subagent), along with whether each tier is priced and which model in the catalog it matches. Tiers that match no model show "Unpriced", and their calls are recorded as unpriced. Tiers left empty in the official Anthropic preset are resolved by Claude Code, and their cost is computed from the model actually called.

For an unpriced tier, choose "Select model" to pick a model from the catalog by hand; the tier's calls are then priced as the selected model, and "Unbind" returns it to automatic matching by model name. The selection is stored only on that Agent provider: it does not change the catalog entry or its official updates. If the selected model is later delisted from the catalog, the tier falls back to matching by model name and shows "Selected model delisted"; you can still unbind it.

Use ArcReel's cost tracking for:

- Project budgets;
- Comparing shot costs;
- Model selection;
- Detecting abnormal calls;
- Analyzing differences between estimates and actual usage.

It is not a substitute for the provider's official invoice.

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

## 14. Documentation Update Principles {#doc-update-principles}

Provider models change frequently, so:

- The README maintains only the capability matrix;
- This document maintains configuration principles;
- The Settings page is authoritative for the specific model list;
- Provider pages are authoritative for official pricing;
- When ArcReel adds or removes a provider, update the matrix, Settings-page help text, and tests together.
