#### Model Capabilities

# Video Generation

Generate videos from text prompts, animate still images, use reference images to guide style and content, edit existing videos, or extend them with natural language. The API supports configurable duration, aspect ratio, and resolution for generated videos — with the SDK handling the asynchronous polling automatically.

## Quick Start

Generate a video with a single API call:

```python customLanguage="pythonXAI"
import os
import xai_sdk

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

response = client.video.generate(
    prompt="A glowing crystal-powered rocket launching from the red dunes of Mars, ancient alien ruins lighting up in the background as it soars into a sky full of unfamiliar constellations",
    model="grok-imagine-video",
    duration=10,
    aspect_ratio="16:9",
    resolution="720p",
)

print(response.url)
```

```python customLanguage="pythonRequests"
import os
import time
import requests

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
}

response = requests.post(
    "https://api.x.ai/v1/videos/generations",
    headers=headers,
    json={
        "model": "grok-imagine-video",
        "prompt": "A glowing crystal-powered rocket launching from the red dunes of Mars, ancient alien ruins lighting up in the background as it soars into a sky full of unfamiliar constellations",
        "duration": 10,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    },
)

request_id = response.json()["request_id"]

# Poll until the video is ready
while True:
    result = requests.get(
        f"https://api.x.ai/v1/videos/{request_id}",
        headers={"Authorization": headers["Authorization"]},
    )
    data = result.json()
    if data["status"] == "done":
        print(data["video"]["url"])
        break
    elif data["status"] == "expired":
        print("Request expired")
        break
    time.sleep(5)
```

```javascript customLanguage="javascriptAISDK"
import { createXai } from "@ai-sdk/xai";
import { experimental_generateVideo as generateVideo } from "ai";
import fs from "fs";

const xai = createXai({ apiKey: process.env.XAI_API_KEY });

const { video } = await generateVideo({
    model: xai.video("grok-imagine-video"),
    prompt: "A glowing crystal-powered rocket launching from the red dunes of Mars, ancient alien ruins lighting up in the background as it soars into a sky full of unfamiliar constellations",
    providerOptions: {
        xai: { duration: 10, aspectRatio: "16:9", resolution: "720p" },
    },
});

// The AI SDK downloads the video automatically — save the raw bytes
fs.writeFileSync("output.mp4", video.uint8Array);
```

```bash
curl -X POST https://api.x.ai/v1/videos/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "A glowing crystal-powered rocket launching from the red dunes of Mars, ancient alien ruins lighting up in the background as it soars into a sky full of unfamiliar constellations",
    "duration": 10,
    "aspect_ratio": "16:9",
    "resolution": "720p"
  }'
```

Video generation is an **asynchronous process** that typically takes up to several minutes to complete. The exact time varies based on:

* **Prompt complexity** — More detailed scenes require additional processing
* **Duration** — Longer videos take more time to generate
* **Resolution** — Higher resolutions (720p vs 480p) increase processing time
* **Video editing** — Editing existing videos adds overhead compared to image-to-video or text-to-video

### How it works

Under the hood, video generation is a two-step process:

1. **Start** — Submit a generation request and receive a `request_id`
2. **Poll** — Repeatedly check the status using the `request_id` until the video is ready

The xAI SDK's `generate()` and `extend()` methods abstract this entirely — they submit your request, poll for the result, and return the completed video response. You don't need to manage request IDs or implement polling logic. For long-running generations, you can [customize the polling behavior](#customize-polling-behavior) with timeout and interval parameters, or [handle polling manually](#handle-polling-manually) for full control over the generation lifecycle.

**REST API users** must implement this two-step flow manually:

**Step 1: Start the generation request**

```bash
curl -X POST https://api.x.ai/v1/videos/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "A glowing crystal-powered rocket launching from Mars"
  }'
```

Response:

```json
{"request_id": "d97415a1-5796-b7ec-379f-4e6819e08fdf"}
```

**Step 2: Poll for the result**

Use the `request_id` to check the status. Keep polling every few seconds until the video is ready:

```bash
curl -X GET "https://api.x.ai/v1/videos/{request_id}" \
  -H "Authorization: Bearer $XAI_API_KEY"
```

The response includes a `status` field with one of these values:

| Status | Description |
|--------|-------------|
| `pending` | Video is still being generated |
| `done` | Video is ready |
| `expired` | Request has expired |
| `failed` | Video generation failed |

Response (when complete):

```json
{
  "status": "done",
  "video": {
    "url": "https://vidgen.x.ai/.../video.mp4",
    "duration": 8,
    "respect_moderation": true
  },
  "model": "grok-imagine-video"
}
```

Videos are returned as temporary URLs — download or process them promptly.

## Generate Videos from Images

Transform a still image into a video by providing a source image along with your prompt. The model animates the image content based on your instructions.

You can provide the source image as:

* A **public URL** pointing to an image
* A **base64-encoded data URI** (e.g., `data:image/jpeg;base64,...`)

The demo below shows this in action — hold to animate a still image:

## Edit Existing Videos

Edit an existing video by providing a source video along with your prompt. The model understands the video content and applies your requested changes.

The demo below shows video editing in action — `grok-imagine-video` delivers high-fidelity edits with strong scene preservation, modifying only what you ask for while keeping the rest of the video intact:

## Generate Videos using Reference Images

Provide one or more reference images to incorporate specific people, objects, clothing, or other visual elements into the generated video. The model uses the reference images as a visual guide, producing a video that features the content from those images — making it ideal for use cases like virtual try-on, product placement, or character-consistent storytelling.

Unlike [image-to-video](#generate-videos-from-images) where the source image becomes the starting frame, reference images influence what appears in the video without locking in the first frame.

Each reference image can be provided as a public HTTPS URL or a base64-encoded data URI.

```python customLanguage="pythonXAI"
import os
import xai_sdk

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

response = client.video.generate(
    prompt="slow zoom in on the white fashion runway stage. then, the model from <IMAGE_1> walks in from the back of the shot from the white opening, and gracefully walk out onto the front of the white stage platform. they wear the shirt from <IMAGE_2> and black flared jeans. they look dramatically at the camera. high quality slow motion shot. fun, playful. skin pores. highly detailed faces. perfect shot. they reach the end of the runway and look at the camera as the camera slowly zooms. subtle smile.",
    model="grok-imagine-video",
    reference_image_urls=[
        "<IMAGE_URL_1>",
        "<IMAGE_URL_2>",
        "<IMAGE_URL_3>",
    ],
    duration=10,
    aspect_ratio="16:9",
    resolution="720p",
)

print(response.url)
```

```python customLanguage="pythonRequests"
import os
import time
import requests

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
}

response = requests.post(
    "https://api.x.ai/v1/videos/generations",
    headers=headers,
    json={
        "model": "grok-imagine-video",
        "prompt": "slow zoom in on the white fashion runway stage. then, the model from <IMAGE_1> walks in from the back of the shot from the white opening, and gracefully walk out onto the front of the white stage platform. they wear the shirt from <IMAGE_2> and black flared jeans. they look dramatically at the camera. high quality slow motion shot. fun, playful. skin pores. highly detailed faces. perfect shot. they reach the end of the runway and look at the camera as the camera slowly zooms. subtle smile.",
        "reference_images": [
            {"url": "<IMAGE_URL_1>"},
            {"url": "<IMAGE_URL_2>"},
            {"url": "<IMAGE_URL_3>"},
        ],
        "duration": 10,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    },
)

request_id = response.json()["request_id"]

while True:
    result = requests.get(
        f"https://api.x.ai/v1/videos/{request_id}",
        headers={"Authorization": headers["Authorization"]},
    )
    data = result.json()
    if data["status"] == "done":
        print(data["video"]["url"])
        break
    elif data["status"] == "expired":
        print("Request expired")
        break
    time.sleep(5)
```

```javascript customLanguage="javascriptAISDK"
import { createXai } from "@ai-sdk/xai";
import { experimental_generateVideo as generateVideo } from "ai";
import fs from "fs";

const xai = createXai({ apiKey: process.env.XAI_API_KEY });

const { video } = await generateVideo({
    model: xai.video("grok-imagine-video"),
    prompt: "slow zoom in on the white fashion runway stage. then, the model from <IMAGE_1> walks in from the back of the shot from the white opening, and gracefully walk out onto the front of the white stage platform. they wear the shirt from <IMAGE_2> and black flared jeans. they look dramatically at the camera. high quality slow motion shot. fun, playful. skin pores. highly detailed faces. perfect shot. they reach the end of the runway and look at the camera as the camera slowly zooms. subtle smile.",
    providerOptions: {
        xai: {
            referenceImages: [
                { url: "<IMAGE_URL_1>" },
                { url: "<IMAGE_URL_2>" },
                { url: "<IMAGE_URL_3>" },
            ],
            duration: 10,
            aspectRatio: "16:9",
            resolution: "720p",
        },
    },
});

// The AI SDK downloads the video automatically — save the raw bytes
fs.writeFileSync("output.mp4", video.uint8Array);
```

```bash
curl -X POST https://api.x.ai/v1/videos/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "slow zoom in on the white fashion runway stage. then, the model from <IMAGE_1> walks in from the back of the shot from the white opening, and gracefully walk out onto the front of the white stage platform. they wear the shirt from <IMAGE_2> and black flared jeans. they look dramatically at the camera. high quality slow motion shot. fun, playful. skin pores. highly detailed faces. perfect shot. they reach the end of the runway and look at the camera as the camera slowly zooms. subtle smile.",
    "reference_images": [
      {"url": "<IMAGE_URL_1>"},
      {"url": "<IMAGE_URL_2>"},
      {"url": "<IMAGE_URL_3>"}
    ],
    "duration": 10,
    "aspect_ratio": "16:9",
    "resolution": "720p"
  }'
```

## Extend Existing Videos

Extend an existing video by providing a source video and a text prompt describing what should happen next. The result is a single video that picks up seamlessly from the last frame of the input and continues with the generated content.

The `duration` parameter controls the length of the **extended portion only**, not the total output. For example, if your input video is 10 seconds and you set `duration` to 5, the returned video will be 15 seconds long (10s original + 5s extension).

```python customLanguage="pythonXAI"
import os
import xai_sdk

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

response = client.video.extend(
    prompt="The shot pans to an over the shoulder perspective. Calm controlled scene.",
    model="grok-imagine-video",
    video_url="<VIDEO_URL>",
    duration=10,
)

print(response.url)
```

```python customLanguage="pythonRequests"
import os
import time
import requests

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
}

response = requests.post(
    "https://api.x.ai/v1/videos/extensions",
    headers=headers,
    json={
        "model": "grok-imagine-video",
        "prompt": "The shot pans to an over the shoulder perspective. Calm controlled scene.",
        "duration": 10,
        "video": {"url": "<VIDEO_URL>"},
    },
)

request_id = response.json()["request_id"]

while True:
    result = requests.get(
        f"https://api.x.ai/v1/videos/{request_id}",
        headers={"Authorization": headers["Authorization"]},
    )
    data = result.json()
    if data["status"] == "done":
        print(data["video"]["url"])
        break
    elif data["status"] == "expired":
        print("Request expired")
        break
    time.sleep(5)
```

```javascript customLanguage="javascriptWithoutSDK"
// Step 1: Start extension request
const response = await fetch("https://api.x.ai/v1/videos/extensions", {
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${process.env.XAI_API_KEY}`,
    },
    body: JSON.stringify({
        model: "grok-imagine-video",
        prompt: "The shot pans to an over the shoulder perspective. Calm controlled scene.",
        duration: 10,
        video: { url: "<VIDEO_URL>" },
    }),
});

const { request_id } = await response.json();
console.log(`Request ID: ${request_id}`);

// Step 2: Poll for results
while (true) {
    const result = await fetch(`https://api.x.ai/v1/videos/${request_id}`, {
        headers: { "Authorization": `Bearer ${process.env.XAI_API_KEY}` },
    });

    const data = await result.json();

    if (data.status === "done") {
        console.log(`Video URL: ${data.video.url}`);
        break;
    } else if (data.status === "expired") {
        console.log("Request expired");
        break;
    } else if (data.status === "failed") {
        console.log("Video generation failed");
        break;
    } else {
        console.log("Still processing...");
        await new Promise(resolve => setTimeout(resolve, 5000));
    }
}
```

```bash
curl -X POST https://api.x.ai/v1/videos/extensions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "The shot pans to an over the shoulder perspective. Calm controlled scene.",
    "duration": 10,
    "video": {"url": "<VIDEO_URL>"}
  }'
```

Video editing uses the `/v1/videos/edits` endpoint and `client.video.generate(video_url=...)` in the SDK, while video extension uses the `/v1/videos/extensions` endpoint and `client.video.extend(video_url=...)`. The same asynchronous polling pattern applies to both, and both SDK methods handle polling automatically.

## Configuration

The video generation API lets you control the output format of your generated videos. You can specify the duration, aspect ratio, and resolution to match your specific use case.

### Duration

Control video length with the `duration` parameter. The allowed range is 1–15 seconds.

Video editing does not support custom `duration`. The edited video retains the duration of the original, which is capped at 8.7 seconds.

### Aspect Ratio

| Ratio | Use case |
|-------|----------|
| `1:1` | Social media, thumbnails |
| `16:9` / `9:16` | Widescreen, mobile, stories (default: `16:9`) |
| `4:3` / `3:4` | Presentations, portraits |
| `3:2` / `2:3` | Photography |

For image-to-video generation, the output defaults to the input image's aspect ratio. If you specify the `aspect_ratio` parameter, it will override this and stretch the image to the desired aspect ratio.

Video editing does not support custom `aspect_ratio` — the output matches the input video's aspect ratio.

### Resolution

| Resolution | Description |
|------------|-------------|
| `720p` | HD quality |
| `480p` | Standard definition, faster processing (default) |

Video editing does not support custom `resolution`. The output resolution matches the input video's resolution, capped at 720p (e.g., a 1080p input will be downsized to 720p).

### Example

```python customLanguage="pythonXAI"
import os
import xai_sdk

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

response = client.video.generate(
    prompt="Timelapse of a flower blooming in a sunlit garden",
    model="grok-imagine-video",
    duration=10,
    aspect_ratio="16:9",
    resolution="720p",
)

print(f"Video URL: {response.url}")
print(f"Duration: {response.duration}s")
```

```python customLanguage="pythonRequests"
import os
import time
import requests

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
}

response = requests.post(
    "https://api.x.ai/v1/videos/generations",
    headers=headers,
    json={
        "model": "grok-imagine-video",
        "prompt": "Timelapse of a flower blooming in a sunlit garden",
        "duration": 10,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    },
)

request_id = response.json()["request_id"]

while True:
    result = requests.get(
        f"https://api.x.ai/v1/videos/{request_id}",
        headers={"Authorization": headers["Authorization"]},
    )
    data = result.json()
    if data["status"] == "done":
        print(f"Video URL: {data['video']['url']}")
        print(f"Duration: {data['video']['duration']}s")
        break
    elif data["status"] == "expired":
        print("Request expired")
        break
    time.sleep(5)
```

```javascript customLanguage="javascriptAISDK"
import { createXai } from "@ai-sdk/xai";
import { experimental_generateVideo as generateVideo } from "ai";
import fs from "fs";

const xai = createXai({ apiKey: process.env.XAI_API_KEY });

const { video } = await generateVideo({
    model: xai.video("grok-imagine-video"),
    prompt: "Timelapse of a flower blooming in a sunlit garden",
    providerOptions: {
        xai: { duration: 10, aspectRatio: "16:9", resolution: "720p" },
    },
});

// The AI SDK downloads the video automatically — save the raw bytes
fs.writeFileSync("output.mp4", video.uint8Array);
```

```bash
curl -X POST https://api.x.ai/v1/videos/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "Timelapse of a flower blooming in a sunlit garden",
    "duration": 10,
    "aspect_ratio": "16:9",
    "resolution": "720p"
  }'
```

### Request Modes

The video generation endpoint supports multiple modes, determined by which fields are set. Only one mode can be active per request:

| Mode | Fields Set | Description |
|------|-----------|-------------|
| Text-to-video | `prompt` only | Generates video from text prompt alone. |
| Image-to-video | `prompt` + `image` | Generates video with the provided image as the first frame. |
| Reference images | `prompt` + `reference_images` | Generates video incorporating content from reference images. |

The following combination is **not allowed** and will return a `400 Bad Request` error:

* `image` + `reference_images` — use one or the other

[Video editing](#edit-existing-videos) and [video extension](#extend-existing-videos) use separate endpoints (`/v1/videos/edits` and `/v1/videos/extensions`) and are not part of the generation request modes above.

## Customize Polling Behavior

When using the SDK's `generate()` or `extend()` methods, you can control how long to wait and how frequently to check for results:

| Python SDK | AI SDK (`providerOptions.xai`) | Description | Default |
|-----------|-------------|-------------|---------|
| `timeout` | `pollTimeoutMs` | Maximum time to wait for the video to complete | 10 minutes |
| `interval` | `pollIntervalMs` | Time between status checks | 100 milliseconds |

```python customLanguage="pythonXAI"
import os
from datetime import timedelta
import xai_sdk

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

response = client.video.generate(
    prompt="Epic cinematic drone shot flying through mountain peaks",
    model="grok-imagine-video",
    duration=15,
    timeout=timedelta(minutes=15),  # Wait up to 15 minutes
    interval=timedelta(seconds=5),  # Check every 5 seconds
)

print(response.url)
```

```javascript customLanguage="javascriptAISDK"
import { createXai } from "@ai-sdk/xai";
import { experimental_generateVideo as generateVideo } from "ai";
import fs from "fs";

const xai = createXai({ apiKey: process.env.XAI_API_KEY });

const { video } = await generateVideo({
    model: xai.video("grok-imagine-video"),
    prompt: "Epic cinematic drone shot flying through mountain peaks",
    providerOptions: {
        xai: {
            duration: 15,
            pollTimeoutMs: 15 * 60 * 1000,  // Wait up to 15 minutes
            pollIntervalMs: 5 * 1000,        // Check every 5 seconds
        },
    },
});

// The AI SDK downloads the video automatically — save the raw bytes
fs.writeFileSync("output.mp4", video.uint8Array);
```

If the video isn't ready within the timeout period, the Python SDK raises a `TimeoutError` and the AI SDK aborts via its `AbortSignal`. For even finer control, use the [manual polling approach](#handle-polling-manually) — the Python SDK provides `start()` and `get()` methods, while the AI SDK supports a custom `abortSignal` for cancellation.

## Handle Polling Manually

For fine-grained control over the generation lifecycle, use `start()` or `extend_start()` to initiate generation/extension requests respectively and `get()` to check status.

The `get()` method returns a response with a `status` field. Import the status enum from the SDK:

```python customLanguage="pythonXAI"
import os
import time
import xai_sdk
from xai_sdk.proto import deferred_pb2

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

# Start the generation request
start_response = client.video.start(
    prompt="A cat lounging in a sunbeam, tail gently swishing",
    model="grok-imagine-video",
    duration=5,
)

print(f"Request ID: {start_response.request_id}")

# Poll for results
while True:
    result = client.video.get(start_response.request_id)
    
    if result.status == deferred_pb2.DeferredStatus.DONE:
        print(f"Video URL: {result.response.video.url}")
        break
    elif result.status == deferred_pb2.DeferredStatus.EXPIRED:
        print("Request expired")
        break
    elif result.status == deferred_pb2.DeferredStatus.FAILED:
        print("Video generation failed")
        break
    elif result.status == deferred_pb2.DeferredStatus.PENDING:
        print("Still processing...")
        time.sleep(5)
```

```python customLanguage="pythonRequests"
import os
import time
import requests

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
}

# Step 1: Start generation
response = requests.post(
    "https://api.x.ai/v1/videos/generations",
    headers=headers,
    json={
        "model": "grok-imagine-video",
        "prompt": "A cat lounging in a sunbeam, tail gently swishing",
        "duration": 5,
    },
)

request_id = response.json()["request_id"]
print(f"Request ID: {request_id}")

# Step 2: Poll for results
while True:
    result = requests.get(
        f"https://api.x.ai/v1/videos/{request_id}",
        headers={"Authorization": headers["Authorization"]},
    )
    data = result.json()

    if data["status"] == "done":
        print(f"Video URL: {data['video']['url']}")
        break
    elif data["status"] == "expired":
        print("Request expired")
        break
    elif data["status"] == "failed":
        print("Video generation failed")
        break
    else:
        print("Still processing...")
        time.sleep(5)
```

```javascript customLanguage="javascriptWithoutSDK"
// Step 1: Start generation
const response = await fetch("https://api.x.ai/v1/videos/generations", {
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${process.env.XAI_API_KEY}`,
    },
    body: JSON.stringify({
        model: "grok-imagine-video",
        prompt: "A cat lounging in a sunbeam, tail gently swishing",
        duration: 5,
    }),
});

const { request_id } = await response.json();
console.log(`Request ID: ${request_id}`);

// Step 2: Poll for results
while (true) {
    const result = await fetch(`https://api.x.ai/v1/videos/${request_id}`, {
        headers: { "Authorization": `Bearer ${process.env.XAI_API_KEY}` },
    });

    const data = await result.json();

    if (data.status === "done") {
        console.log(`Video URL: ${data.video.url}`);
        break;
    } else if (data.status === "expired") {
        console.log("Request expired");
        break;
    } else if (data.status === "failed") {
        console.log("Video generation failed");
        break;
    } else {
        console.log("Still processing...");
        await new Promise(resolve => setTimeout(resolve, 5000));
    }
}
```

```bash
# Step 1: Start generation
curl -X POST https://api.x.ai/v1/videos/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "A cat lounging in a sunbeam, tail gently swishing",
    "duration": 5
  }'

# Response: {"request_id": "{request_id}"}

# Step 2: Poll for results
curl -X GET https://api.x.ai/v1/videos/{request_id} \
  -H "Authorization: Bearer $XAI_API_KEY"
```

The available status values are:

| Proto Value | Description |
|-------------|-------------|
| `deferred_pb2.DeferredStatus.PENDING` | Video is still being generated |
| `deferred_pb2.DeferredStatus.DONE` | Video is ready |
| `deferred_pb2.DeferredStatus.EXPIRED` | Request has expired |
| `deferred_pb2.DeferredStatus.FAILED` | Video generation failed |

## Error Handling

When using the SDK's `generate()` or `extend()` methods, video generation failures are raised as a `VideoGenerationError` exception. This exception includes a `code` and `message` describing what went wrong. Import it from `xai_sdk.video`:

```python customLanguage="pythonXAI"
import os
import xai_sdk
from xai_sdk.video import VideoGenerationError

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

try:
    response = client.video.generate(
        prompt="A cat lounging in a sunbeam, tail gently swishing",
        model="grok-imagine-video",
        duration=5,
    )
    print(response.url)
except VideoGenerationError as e:
    print(f"Error code: {e.code}")
    print(f"Error message: {e.message}")
```

The `VideoGenerationError` exception has the following attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `code` | `str` | An error code identifying the failure reason |
| `message` | `str` | A human-readable message describing the failure |

You can combine this with `TimeoutError` handling for comprehensive error coverage:

```python customLanguage="pythonXAI"
import os
import xai_sdk
from xai_sdk.video import VideoGenerationError

client = xai_sdk.Client(api_key=os.getenv("XAI_API_KEY"))

try:
    response = client.video.generate(
        prompt="A cat lounging in a sunbeam, tail gently swishing",
        model="grok-imagine-video",
        duration=5,
    )
    print(response.url)
except VideoGenerationError as e:
    print(f"Generation failed [{e.code}]: {e.message}")
except TimeoutError:
    print("Generation timed out — try increasing the timeout or simplifying the prompt")
```

## Response Details

The xAI SDK exposes additional metadata on the response object beyond the video URL.

**Moderation** — Check whether the generated video passed content moderation:

```python customLanguage="pythonXAI"
if response.respect_moderation:
    print(response.url)
else:
    print("Video filtered by moderation")
```

**Duration** — Get the actual duration of the generated video:

```python customLanguage="pythonXAI"
print(f"Duration: {response.duration} seconds")
```

**Model** — Get the actual model used (resolving any aliases):

```python customLanguage="pythonXAI"
print(f"Model: {response.model}")
```

## Concurrent Requests

When you need to generate multiple videos or apply several edits to the same source video, use `AsyncClient` with `asyncio.gather` to fire requests concurrently. Since video generation and editing are long-running processes, running requests in parallel is significantly faster than issuing them sequentially.

The example below applies three different edits to the same source video concurrently:

```python customLanguage="pythonXAI"
import os
import asyncio
import xai_sdk

async def edit_concurrently():
    client = xai_sdk.AsyncClient(api_key=os.getenv("XAI_API_KEY"))

    source_video = "https://data.x.ai/docs/video-generation/portrait-wave.mp4"

    # Each request applies a different edit to the same video
    prompts = [
        "Give the woman a silver necklace",
        "Change the color of the woman's outfit to red",
        "Give the woman a wide-brimmed black hat",
    ]

    # Fire all edit requests concurrently
    tasks = [
        client.video.generate(
            prompt=prompt,
            model="grok-imagine-video",
            video_url=source_video,
        )
        for prompt in prompts
    ]

    results = await asyncio.gather(*tasks)

    for prompt, result in zip(prompts, results):
        print(f"{prompt}: {result.url}")

asyncio.run(edit_concurrently())
```

## Pricing

Video generation uses per-second pricing. Longer videos cost more, and both duration and resolution affect the total cost.

For full pricing details on the `grok-imagine-video` model, see the [model page](/developers/models).

## Limitations

* **Maximum duration:** 15 seconds for generation, 8.7 seconds for editing input videos, 2–10 seconds for video extensions (input video must be 2–15 seconds)
* **URL expiration:** Generated URLs are ephemeral and should not be relied upon for long-term storage
* **Resolutions:** 480p or 720p
* **Content moderation:** Videos are subject to content policy review

## Related

* [Models](/developers/models) — Available video models and pricing
* [Image Generation](/developers/model-capabilities/images/generation) — Generate still images from text
* [API Reference](/developers/rest-api-reference) — Full endpoint documentation
* [Imagine API Landing Page](https://x.ai/api/imagine) — Showcase of the Imagine API in action
