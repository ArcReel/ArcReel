#### Model Capabilities

# Image Generation

Generate images from text prompts, edit existing images with natural language, or iteratively refine images through multi-turn conversations. The API supports batch generation of multiple images, and control over aspect ratio and resolution.

## Quick Start

Generate an image with a single API call:

```python customLanguage="pythonXAI"
import xai_sdk

client = xai_sdk.Client()

response = client.image.sample(
    prompt="A collage of London landmarks in a stenciled street‑art style",
    model="grok-imagine-image",
)

print(response.url)
```

```bash
curl -X POST https://api.x.ai/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-image",
    "prompt": "A collage of London landmarks in a stenciled street‑art style"
  }'
```

```python customLanguage="pythonOpenAISDK"
from openai import OpenAI

client = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key="YOUR_API_KEY",
)

response = client.images.generate(
    model="grok-imagine-image",
    prompt="A collage of London landmarks in a stenciled street‑art style",
)

print(response.data[0].url)
```

```javascript customLanguage="javascriptOpenAISDK"
import OpenAI from "openai";

const client = new OpenAI({
    apiKey: process.env.XAI_API_KEY,
    baseURL: 'https://api.x.ai/v1',
});

const response = await client.images.generate({
    model: "grok-imagine-image",
    prompt: "A collage of London landmarks in a stenciled street‑art style",
});

console.log(response.data[0].url);
```

```javascript customLanguage="javascriptAISDK"
import { xai } from "@ai-sdk/xai";
import { generateImage } from "ai";

const { image } = await generateImage({
    model: xai.image("grok-imagine-image"),
    prompt: "A collage of London landmarks in a stenciled street‑art style",
});

console.log(image.base64);
```

Images are returned as URLs by default. URLs are temporary, so download or process promptly. You can also request [base64 output](#base64-output) for embedding images directly.

## Image Editing

Edit an existing image by providing a source image along with your prompt. The model understands the image content and applies your requested changes.

The OpenAI SDK's `images.edit()` method is not supported for image editing because it uses `multipart/form-data`, while the xAI API requires `application/json`. Use the xAI SDK, Vercel AI SDK, or direct HTTP requests instead.

With the xAI SDK, use the same `sample()` method — just add the `image_url` parameter:

```python customLanguage="pythonXAI"
import base64
import xai_sdk

client = xai_sdk.Client()

# Load image from file and encode as base64
with open("photo.png", "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")

response = client.image.sample(
    prompt="Render this as a pencil sketch with detailed shading",
    model="grok-imagine-image",
    image_url=f"data:image/png;base64,{image_data}",
)

print(response.url)
```

```bash
# Using a public URL as the source image
curl -X POST https://api.x.ai/v1/images/edits \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-image",
    "prompt": "Render this as a pencil sketch with detailed shading",
    "image": {
      "url": "https://docs.x.ai/assets/api-examples/images/style-realistic.png",
      "type": "image_url"
    }
  }'
```

```javascript customLanguage="javascriptAISDK"
import { xai } from "@ai-sdk/xai";
import { generateImage } from "ai";
import fs from "fs";

// Load image and encode as base64
const imageBuffer = fs.readFileSync("photo.png");
const base64Image = imageBuffer.toString("base64");

const { image } = await generateImage({
    model: xai.image("grok-imagine-image"),
    prompt: "Render this as a pencil sketch with detailed shading",
    providerOptions: {
        xai: {
            image: `data:image/png;base64,${base64Image}`,
        },
    },
});

console.log(image.base64);
```

You can provide the source image as:

* A **public URL** pointing to an image
* A **base64-encoded data URI** (e.g., `data:image/jpeg;base64,...`)

## Editing with Multiple Images

You can add up to 5 images for editing. You can specify the images in the order they are sent in the request. By default, the aspect ratio of the output image follows the first input image. You can override this by setting the `aspect_ratio` parameter to a specific ratio (e.g., `"1:1"`, `"16:9"`).

## Multi-Turn Editing

Chain multiple edits together by using each output as the input for the next. This enables iterative refinement — start with a base image and progressively add details, adjust styles, or make corrections.

## Style Transfer

The `grok-imagine-image` model excels across a wide range of visual styles — from ultra-realistic photography to anime, oil paintings, pencil sketches, and beyond. Transform existing images by simply describing the desired aesthetic in your prompt.

## Concurrent Requests

When you need to generate multiple images with **different prompts** — such as applying various style transfers to the same source image, or generating unrelated images in parallel — use `AsyncClient` with `asyncio.gather` to fire requests concurrently. This is significantly faster than issuing them one at a time.

If you want multiple variations from the **same prompt**, use [`sample_batch()` with the `n` parameter](#multiple-images) instead. That generates all images in a single request and is the most efficient approach for same-prompt generation.

```python customLanguage="pythonXAI"
import asyncio
import xai_sdk

async def generate_concurrently():
    client = xai_sdk.AsyncClient()

    source_image = "https://docs.x.ai/assets/api-examples/images/style-realistic.png"

    # Each request uses a different prompt
    prompts = [
        "Render this image as an oil painting in the style of impressionism",
        "Render this image as a pencil sketch with detailed shading",
        "Render this image as pop art with bold colors and halftone dots",
        "Render this image as a watercolor painting with soft edges",
    ]

    # Fire all requests concurrently
    tasks = [
        client.image.sample(
            prompt=prompt,
            model="grok-imagine-image",
            image_url=source_image,
        )
        for prompt in prompts
    ]

    results = await asyncio.gather(*tasks)

    for prompt, result in zip(prompts, results):
        print(f"{prompt}: {result.url}")

asyncio.run(generate_concurrently())
```

## Configuration

### Multiple Images

Generate multiple images in a single request using the `sample_batch()` method and the `n` parameter. This returns a list of `ImageResponse` objects.

```python customLanguage="pythonXAI"
import xai_sdk

client = xai_sdk.Client()

responses = client.image.sample_batch(
    prompt="A futuristic city skyline at night",
    model="grok-imagine-image",
    n=4,
)

for i, image in enumerate(responses):
    print(f"Variation {i + 1}: {image.url}")
```

```python customLanguage="pythonOpenAISDK"
from openai import OpenAI

client = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key="YOUR_API_KEY",
)

response = client.images.generate(
    model="grok-imagine-image",
    prompt="A futuristic city skyline at night",
    n=4,
)

for i, image in enumerate(response.data):
    print(f"Variation {i + 1}: {image.url}")
```

```javascript customLanguage="javascriptOpenAISDK"
import OpenAI from "openai";

const client = new OpenAI({
    apiKey: process.env.XAI_API_KEY,
    baseURL: "https://api.x.ai/v1",
});

const response = await client.images.generate({
    model: "grok-imagine-image",
    prompt: "A futuristic city skyline at night",
    n: 4,
});

response.data.forEach((image, i) => {
    console.log(`Variation ${i + 1}: ${image.url}`);
});

```

```javascript customLanguage="javascriptAISDK"
import { xai } from "@ai-sdk/xai";
import { generateImage } from "ai";

const { images } = await generateImage({
    model: xai.image("grok-imagine-image"),
    prompt: "A futuristic city skyline at night",
    n: 4,
});

images.forEach((image, i) => {
    console.log(`Variation ${i + 1}: ${image.base64.slice(0, 50)}...`);
});

```

```bash
curl -X POST https://api.x.ai/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-image",
    "prompt": "A futuristic city skyline at night",
    "n": 4
  }'
```

### Aspect Ratio

Control image dimensions with the `aspect_ratio` parameter. This works for image generation and image editing with multiple images.
For image editing with single image, the output aspect ratio respects the input image's aspect ratio.

| Ratio | Use case |
|-------|----------|
| `1:1` | Social media, thumbnails |
| `16:9` / `9:16` | Widescreen, mobile, stories |
| `4:3` / `3:4` | Presentations, portraits |
| `3:2` / `2:3` | Photography |
| `2:1` / `1:2` | Banners, headers |
| `19.5:9` / `9:19.5` | Modern smartphone displays |
| `20:9` / `9:20` | Ultra-wide displays |
| `auto` | Model auto-selects the best ratio for the prompt |

```python customLanguage="pythonXAI"
import xai_sdk

client = xai_sdk.Client()

response = client.image.sample(
    prompt="Mountain landscape at sunrise",
    model="grok-imagine-image",
    aspect_ratio="16:9",
)

print(response.url)
```

```python customLanguage="pythonOpenAISDK"
from openai import OpenAI

client = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key="YOUR_API_KEY",
)

response = client.images.generate(
    model="grok-imagine-image",
    prompt="Mountain landscape at sunrise",
    extra_body={"aspect_ratio": "16:9"},
)

print(response.data[0].url)
```

```javascript customLanguage="javascriptOpenAISDK"
import OpenAI from "openai";

const client = new OpenAI({
    apiKey: process.env.XAI_API_KEY,
    baseURL: "https://api.x.ai/v1",
});

const response = await client.images.generate({
    model: "grok-imagine-image",
    prompt: "Mountain landscape at sunrise",
    // @ts-expect-error — xAI-specific parameter
    aspect_ratio: "16:9",
});

console.log(response.data[0].url);
```

```javascript customLanguage="javascriptAISDK"
import { xai } from "@ai-sdk/xai";
import { generateImage } from "ai";

const { image } = await generateImage({
    model: xai.image("grok-imagine-image"),
    prompt: "Mountain landscape at sunrise",
    aspectRatio: "16:9",
});

console.log(image.base64);
```

```bash
curl -X POST https://api.x.ai/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-image",
    "prompt": "Mountain landscape at sunrise",
    "aspect_ratio": "16:9"
  }'
```

### Resolution

You can specify different resolutions of the output image. Currently supported image resolutions are:

* 1k
* 2k

```python customLanguage="pythonXAI"
import xai_sdk

client = xai_sdk.Client()

response = client.image.sample(
    prompt="An astronaut performing EVA in LEO.",
    model="grok-imagine-image",
    resolution="2k"
)

print(response.url)
```

```python customLanguage="pythonOpenAISDK"
from openai import OpenAI

client = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key="YOUR_API_KEY",
)

response = client.images.generate(
    model="grok-imagine-image",
    prompt="An astronaut performing EVA in LEO.",
    extra_body={"resolution": "2k"},
)

print(response.data[0].url)
```

```javascript customLanguage="javascriptOpenAISDK"
import OpenAI from "openai";

const client = new OpenAI({
    apiKey: process.env.XAI_API_KEY,
    baseURL: "https://api.x.ai/v1",
});

const response = await client.images.generate({
    model: "grok-imagine-image",
    prompt: "An astronaut performing EVA in LEO.",
    // @ts-expect-error — xAI-specific parameter
    resolution: "2k",
});

console.log(response.data[0].url);
```

```bash
curl -X POST https://api.x.ai/v1/images/generations \
-H "Content-Type: application/json" \
-H "Authorization: Bearer $XAI_API_KEY" \
-d '{
    "model": "grok-imagine-image",
    "prompt": "An astronaut performing EVA in LEO.",
    "resolution": "2k"
}'
```

### Base64 Output

For embedding images directly without downloading, request base64:

```python customLanguage="pythonXAI"
import xai_sdk

client = xai_sdk.Client()

response = client.image.sample(
    prompt="A serene Japanese garden",
    model="grok-imagine-image",
    image_format="base64",
)

# Save to file
with open("garden.jpg", "wb") as f:
    f.write(response.image)
```

```python customLanguage="pythonOpenAISDK"
import base64
from openai import OpenAI

client = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key="YOUR_API_KEY",
)

response = client.images.generate(
    model="grok-imagine-image",
    prompt="A serene Japanese garden",
    response_format="b64_json",
)

# Save to file
image_bytes = base64.b64decode(response.data[0].b64_json)
with open("garden.jpg", "wb") as f:
    f.write(image_bytes)
```

```javascript customLanguage="javascriptOpenAISDK"
import OpenAI from "openai";
import fs from "fs";

const client = new OpenAI({
    apiKey: process.env.XAI_API_KEY,
    baseURL: "https://api.x.ai/v1",
});

const response = await client.images.generate({
    model: "grok-imagine-image",
    prompt: "A serene Japanese garden",
    response_format: "b64_json",
});

// Save to file
const imageBuffer = Buffer.from(response.data[0].b64_json, "base64");
fs.writeFileSync("garden.jpg", imageBuffer);
```

```javascript customLanguage="javascriptAISDK"
import { xai } from "@ai-sdk/xai";
import { generateImage } from "ai";
import fs from "fs";

const { image } = await generateImage({
    model: xai.image("grok-imagine-image"),
    prompt: "A serene Japanese garden",
});

// Save to file (AI SDK returns base64 by default)
const imageBuffer = Buffer.from(image.base64, "base64");
fs.writeFileSync("garden.jpg", imageBuffer);
```

```bash
curl -X POST https://api.x.ai/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-image",
    "prompt": "A serene Japanese garden",
    "response_format": "b64_json"
  }'
```

### Response Details

The xAI SDK exposes additional metadata on the response object beyond the image URL or base64 data.

**Moderation** — Check whether the generated image passed content moderation:

```python customLanguage="pythonXAI"
if response.respect_moderation:
    print(response.url)
else:
    print("Image filtered by moderation")
```

**Model** — Get the actual model used (resolving any aliases):

```python customLanguage="pythonXAI"
print(f"Model: {response.model}")
```

## Pricing

Image generation uses flat per-image pricing rather than token-based pricing like text models. Each generated image incurs a fixed fee regardless of prompt length.

For image editing, you are charged for both the input image and the generated output image.

For full pricing details on the `grok-imagine-image` model, see the [model page](/developers/models/grok-imagine-image).

## Limitations

* **Maximum images per request:** 10
* **URL expiration:** Generated URLs are temporary
* **Content moderation:** Images are subject to content policy review

## Related

* [Models](/developers/models) — Available image models
* [Video Generation](/developers/model-capabilities/video/generation) — Animate generated images
* [API Reference](/developers/rest-api-reference) — Full endpoint documentation
* [Imagine API Landing Page](https://x.ai/api/imagine) — Showcase of the Imagine API in action
