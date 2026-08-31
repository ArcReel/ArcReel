---
id: providers
title: Provider and Model Configuration
sidebar_position: 3
---

# Provider and Model Configuration {#providers}

ArcReel treats the AI assistant, text generation, image generation, video generation, and TTS as separate capabilities. You can choose different providers for different projects and media types instead of tying the entire production pipeline to one platform.

## 1. Start by Distinguishing Two Provider Categories {#two-provider-categories}

### 1.1 AI Assistant Providers {#assistant-providers}

The AI assistant is responsible for:

- Understanding user intent;
- Analyzing novels and screenplays;
- Extracting characters, scenes, and props;
- Planning episodes;
- Normalizing screenplays;
- Orchestrating subsequent generation tasks.

ArcReel's Agent Runtime is based on the Claude Agent SDK and supports Anthropic's official service as well as compatible configurations supported by the project.

The AI assistant provider does not necessarily handle the actual image and video generation.

### 1.2 Media and Text Providers {#media-and-text-providers}

These providers are responsible for:

- Structured text generation;
- Image generation and editing;
- Video generation;
- TTS.

A complete project can use a combination of providers, for example:

- AI assistant: a high-quality reasoning model;
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
| Gemini | ✅ | ✅ | ✅ | — | Multimodal text, reference images, and video generation |
| Volcengine Ark | ✅ | ✅ | ✅ | — | Text, image, and video generation in mainland China network environments |
| Grok | ✅ | ✅ | ✅ | — | Text, image, and video generation |
| OpenAI | ✅ | ✅ | ✅ | — | Text, image, and video generation |
| Vidu | — | ✅ | ✅ | — | Image, image-to-video, and reference-to-video generation |
| DashScope | ✅ | ✅ | ✅ | ✅ | Text, image, video, and voice-over TTS |
| MiniMax | ✅ | ✅ | ✅ | — | Text, image, and video generation |
| Kling | — | ✅ | ✅ | — | Image, image-to-video, and reference-to-video generation |
| Agnes | ✅ | ✅ | ✅ | — | Text, image, and video generation |
| Custom Providers | Interface-dependent | Interface-dependent | Interface-dependent | Interface-dependent | Private gateways, local models, or third-party compatible services |

The built-in OpenAI provider offers text, image, and video capabilities. OpenAI-compatible TTS is connected through a Custom Provider's TTS call endpoint.

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

ArcReel's voice-over capability can use DashScope Qwen TTS or a compatible TTS interface.

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

The model discovery protocol only determines which type of model-listing interface ArcReel uses. How each model is actually called is determined by its bound call endpoint. ArcReel can infer the media type and call endpoint from the model name, but compatibility with a protocol does not mean that every capability and parameter is available. You can adjust these manually before saving, and you should validate each of the following after adding the provider:

1. Text connectivity;
2. Structured output;
3. Image generation;
4. Video task creation;
5. Video task queries;
6. TTS;
7. Error codes and rate-limiting behavior.

### 10.1 Three Endpoint Test Modes {#custom-endpoint-tests}

The call endpoint detail page in Settings offers three test modes, ordered from lowest to highest risk:

- **Check a response**: paste a provider response from the submit, progress, or fetch stage to inspect every
  extraction path, status mapping, and error field. This does not contact the provider or incur a charge.
- **Preview the request**: render the URL, method, headers, and body from sample parameters so you can compare
  them with the provider documentation. This also does not contact the provider or incur a charge.
- **Test connection**: submit one real generation and track it to a terminal state. A billing badge identifies
  that this mode incurs one API call. Endpoints with required assets need the corresponding first frame, last
  frame, reference images, or reference audio before the test can start.

You can cancel a connection test while it is running. Cancellation stops ArcReel from tracking that test and
removes its local result, but the provider's remote job may continue and charges already incurred remain in the
ledger. A successful artifact plays directly in the result card, and the call number links to the spend ledger.
The server retains terminal results and artifacts for 24 hours; they are unavailable after that period.

### 10.2 Adapt a Custom Video Call Endpoint with an Agent {#custom-endpoint-agent}

When a provider submits jobs as JSON and then polls JSON results by task ID, you can delegate the adaptation to
an external Agent such as Claude Code. Signature-based authentication, multipart requests sent to the provider,
and routing by asset shape are outside the first version of declarative definitions.

Install the public skill:

```bash
npx skills add ArcReel/skills
```

Select `adapt-custom-endpoint` from the installation list. You can also
[view or download the same skill source](https://github.com/ArcReel/ArcReel/tree/main/agent_runtime_profile/.claude/skills/adapt-custom-endpoint).

First use the `setup-arcreel-skills` skill to connect ArcReel MCP. It persists the same connection in the current
workspace so the local `adapt-custom-endpoint` script can reuse it across sessions.

Then ask the Agent to read the provider documentation and use this skill. It follows the sequence “write a
definition → run the shared validator → check responses offline → preview requests → run a connection test →
save.” The definition reference and thin HTTP script are downloaded with the skill; the adaptation flow adds no
MCP or SDK tool. Response checks and request previews do not contact the provider. A connection test performs a real
generation and may incur charges, so the Agent must obtain your explicit approval first. When a definition with
the same lineage exists, the Agent may save a copy and report it; overwriting the existing endpoint always
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
