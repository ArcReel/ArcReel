#### Key Information

# Models and Pricing

An overview of our models' capabilities and their associated pricing.

| Model | Modalities | Capabilities | Context | Rate Limits | Pricing [in (cached in) / out] |
| --- | --- | --- | --- | --- | --- |
| grok-4.20-multi-agent-beta-0309 | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $2.00 ($0.20) / $6.00 |
| grok-4.20-multi-agent-beta-0309 | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $2.00 ($0.20) / $6.00 |
| grok-4.20-beta-0309-reasoning | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $2.00 ($0.20) / $6.00 |
| grok-4.20-beta-0309-reasoning | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $2.00 ($0.20) / $6.00 |
| grok-4.20-beta-0309-non-reasoning | text, image → text | functions, structured | 2,000,000 | 4M TPM, 607 RPM | $2.00 ($0.20) / $6.00 |
| grok-4.20-beta-0309-non-reasoning | text, image → text | functions, structured | 2,000,000 | 4M TPM, 607 RPM | $2.00 ($0.20) / $6.00 |
| grok-4-1-fast-reasoning | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-1-fast-reasoning | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-1-fast-non-reasoning | text, image → text | functions, structured | 2,000,000 | 4M TPM, 607 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-1-fast-non-reasoning | text, image → text | functions, structured | 2,000,000 | 4M TPM, 607 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-1-fast-non-reasoning | text, image → text | functions, structured | 2,000,000 | 4M TPM, 480 RPM | $0.20 ($0.05) / $0.50 |
| grok-code-fast-1 | text → text | functions, structured, reasoning | 256,000 | 2M TPM, 2,125 RPM | $0.20 ($0.02) / $1.50 |
| grok-code-fast-1 | text → text | functions, structured, reasoning | 256,000 | 2M TPM, 2,125 RPM | $0.20 ($0.02) / $1.50 |
| grok-4-0709 | text, image → text | functions, structured, reasoning | 256,000 | 20M TPM, 500 RPM | $3.00 ($0.75) / $15.00 |
| grok-4-0709 | text, image → text | functions, structured, reasoning | 256,000 | 20M TPM, 500 RPM | $3.00 ($0.75) / $15.00 |
| grok-3 | text → text | functions, structured | 131,072 | 4M TPM, 900 RPM | $3.00 ($0.75) / $15.00 |
| grok-3 | text → text | functions, structured | 131,072 | 4M TPM, 900 RPM | $3.00 ($0.75) / $15.00 |
| grok-3-mini | text → text | functions, structured, reasoning | 131,072 | 4M TPM, 1,450 RPM | $0.30 ($0.07) / $0.50 |
| grok-3-mini | text → text | functions, structured, reasoning | 131,072 | 4M TPM, 1,450 RPM | $0.30 ($0.07) / $0.50 |
| grok-4-fast-reasoning | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-fast-non-reasoning | text, image → text | functions, structured | 2,000,000 | 10M TPM, 620 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-fast-reasoning | text, image → text | functions, structured, reasoning | 2,000,000 | 4M TPM, 607 RPM | $0.20 ($0.05) / $0.50 |
| grok-4-fast-non-reasoning | text, image → text | functions, structured | 2,000,000 | 10M TPM, 620 RPM | $0.20 ($0.05) / $0.50 |
| grok-imagine-image-pro | text, image → image | - | - | 30 RPM | $0.07/image |
| grok-imagine-image | text, image → image | - | - | 300 RPM | $0.02/image |
| grok-imagine-image-pro | text, image → image | - | - | 30 RPM | $0.07/image |
| grok-imagine-image | text, image → image | - | - | 300 RPM | $0.02/image |
| grok-imagine-video | text, image, video → video | - | - | 60 RPM | $0.050/sec |
| grok-imagine-video | text, image, video → video | - | - | 60 RPM | $0.050/sec |


When moving from `grok-3`/`grok-3-mini` to `grok-4`, please note the following differences:


Grok 4.20 models do not support the `logprobs` field. If you specify `logprobs` in your request, it will be ignored.

## Tools Pricing

Requests which make use of xAI provided [server-side tools](/developers/tools/overview) are priced based on two components: **token usage** and **server-side tool invocations**. Since the agent autonomously decides how many tools to call, costs scale with query complexity.

### Token Costs

All standard token types are billed at the [rate](#model-pricing) for the model used in the request:

* **Input tokens**: Your query and conversation history
* **Reasoning tokens**: Agent's internal thinking and planning
* **Completion tokens**: The final response
* **Image tokens**: Visual content analysis (when applicable)
* **Cached prompt tokens**: Prompt tokens that were served from cache rather than recomputed

### Tool Invocation Costs

| Tool | Description | Cost / 1k Calls | Tool Name |
| --- | --- | --- | --- |
| Web Search | Search the internet and browse web pages | $5 | `web_search` |
| X Search | Search X posts, user profiles, and threads | $5 | `x_search` |
| Code Execution | Run Python code in a sandboxed environment | $5 | `code_execution`, `code_interpreter[object Object]` |
| File Attachments | Search through files attached to messages | $10 | `attachment_search` |
| Collections Search | Query your uploaded document collections (RAG) | $2.50 | `collections_search`, `file_search[object Object]` |
| Image Understanding | Analyze images found during Web Search and X Search\* | Token-based | `view_image` |
| X Video Understanding | Analyze videos found during X Search\* | Token-based | `view_x_video` |
| Remote MCP Tools | Connect and use custom MCP tool servers | Token-based | *(set by MCP server)* |
\[object Object] All tool names work in the Responses API. In the gRPC API (Python xAI SDK), `code_interpreter` and `file_search` are not supported.
\* Only applies to images and videos found by search tools — not to images passed directly in messages.

For the view image and view x video tools, you will not be charged for the tool invocation itself but will be charged for the image tokens used to process the image or video.

For Remote MCP tools, you will not be charged for the tool invocation but will be charged for any tokens used.

For more information on using Tools, please visit [our guide on Tools](/developers/tools/overview).

## Batch API Pricing

The [Batch API](/developers/advanced-api-usage/batch-api) lets you process large volumes of requests asynchronously at **50% of standard pricing** — effectively cutting your token costs in half. Batch requests are queued and processed in the background, with most completing within 24 hours.

| | Real-time API | Batch API |
|---|---|---|
| **Token pricing** | Standard rates | **50% off** standard rates |
| **Response time** | Immediate (seconds) | Typically within 24 hours |
| **Rate limits** | Per-minute limits apply | Requests don't count towards rate limits |

The 50% discount applies to all token types — input tokens, output tokens, cached tokens, and reasoning tokens. To see batch pricing for a specific model, visit the model's detail page and toggle **"Show batch API pricing"**.

Batch API pricing applies to text and language models only. Image and video generation models are not supported in the Batch API. See [Batch API documentation](/developers/advanced-api-usage/batch-api) for full details, limitations, and usage examples.

## Voice API Pricing

### Voice Agent API (Realtime)

The [Voice Agent API](/developers/model-capabilities/audio/voice-agent) enables real-time voice conversations over WebSocket, billed at a flat rate per minute of connection time.

| | Details |
|---|---|
| **Pricing** | $0.05 / minute ($3.00 / hour) |
| **Concurrent sessions** | 100 per team |
| **Max session duration** | 30 minutes |
| **Capabilities** | Function calling (web search, X search, collections, custom functions) |

When using the Voice Agent API with tools such as function calling, web search, X search, or collections, you will be charged for the tool invocations in addition to the per-minute voice session cost. See [Tool Invocation Costs](#tool-invocation-costs) above for tool pricing details.

For more details on how to get started, see the [Voice Agent API documentation](/developers/model-capabilities/audio/voice-agent).

### Text to Speech API (Beta)

The [Text to Speech API](/developers/model-capabilities/audio/text-to-speech) converts text into natural speech, billed per input character.

| | Details |
|---|---|
| **Pricing** | $4.20 / 1M characters (Beta Pricing)|
| **Concurrent requests** | 100 per team |
| **Capabilities** | Multiple voices, streaming and batch output, MP3 / WAV / PCM / μ-law / A-law formats |

The Text to Speech API is currently in **beta**. Pricing and rate limits may change when the API becomes generally available.

## Usage Guidelines Violation Fee

When your request is deemed to be in violation of our usage guideline by our system, we will still charge for the generation of the request.

For violations that are caught before generation in the Responses API, we will charge a $0.05 usage guideline violation fee per request.

## Additional Information Regarding Models

* **No access to realtime events without search tools enabled**
  * Grok has no knowledge of current events or data beyond what was present in its training data.
  * To incorporate realtime data with your request, enable server-side search tools (Web Search / X Search). See [Web Search](/developers/tools/web-search) and [X Search](/developers/tools/x-search).
* **Chat models**
  * No role order limitation: You can mix `system`, `user`, or `assistant` roles in any sequence for your conversation context.
* **Image input models**
  * Maximum image size: `20MiB`
  * Maximum number of images: No limit
  * Supported image file types: `jpg/jpeg` or `png`.
  * Any image/text input order is accepted (e.g. text prompt can precede image prompt)

The knowledge cut-off date of Grok 3 and Grok 4 is November, 2024.

## Model Aliases

Some models have aliases to help users automatically migrate to the next version of the same model. In general:

* `<modelname>` is aliased to the latest stable version.
* `<modelname>-latest` is aliased to the latest version. This is suitable for users who want to access the latest features.
* `<modelname>-<date>` refers directly to a specific model release. This will not be updated and is for workflows that demand consistency.

For most users, the aliased `<modelname>` or `<modelname>-latest` are recommended, as you would receive the latest features automatically.

## Billing and Availability

Your model access might vary depending on various factors such as geographical location, account limitations, etc.

For how the **bills are charged**, visit [Manage Billing](/console/billing) for more information.

For the most up-to-date information on **your team's model availability**, visit [Models Page](https://console.x.ai/team/default/models) on xAI Console.

## Model Input and Output

Each model can have one or multiple input and output capabilities.
The input capabilities refer to which type(s) of prompt can the model accept in the request message body.
The output capabilities refer to which type(s) of completion will the model generate in the response message body.

This is a prompt example for models with `text` input capability:

```json
[
  {
    "role": "system",
    "content": "You are Grok, a chatbot inspired by the Hitchhiker's Guide to the Galaxy."
  },
  {
    "role": "user",
    "content": "What is the meaning of life, the universe, and everything?"
  }
]
```

This is a prompt example for models with `text` and `image` input capabilities:

```json
[
  {
    "role": "user",
    "content": [
      {
        "type": "image_url",
        "image_url": {
          "url": "data:image/jpeg;base64,<base64_image_string>",
          "detail": "high"
        }
      },
      {
        "type": "text",
        "text": "Describe what's in this image."
      }
    ]
  }
]
```

This is a prompt example for models with `text` input and `image` output capabilities:

```json
// The entire request body
{
  "model": "grok-4",
  "prompt": "A cat in a tree",
  "n": 4
}
```

## Context Window

The context window determines the maximum amount of tokens accepted by the model in the prompt.

For more information on how token is counted, visit [Consumption and Rate Limits](/developers/rate-limits).

If you are sending the entire conversation history in the prompt for use cases like chat assistant, the sum of all the prompts in your conversation history must be no greater than the context window.

## Cached prompt tokens

Trying to run the same prompt multiple times? You can now use cached prompt tokens to incur less cost on repeated prompts. By reusing stored prompt data, you save on processing expenses for identical requests. Enable caching in your settings and start saving today!

The caching is automatically enabled for all requests without user input. You can view the cached prompt token consumption in [the `"usage"` object](/developers/rate-limits#checking-token-consumption).

For details on the pricing, please refer to the pricing table above, or on [xAI Console](https://console.x.ai).
