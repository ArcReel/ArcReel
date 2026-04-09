# Seedance Video Generation Model Features and Python Development Guide

The Seedance model has excellent semantic understanding capabilities and can quickly generate high-quality video clips based on multi-modal inputs such as text, images, videos, and audio from users. This document introduces the general core capabilities of the video generation model and guides you through calling the Video Generation API in Python.

## 1. Model Capability Overview

The table below shows all capabilities supported by each Seedance model to help you compare and choose.

| **Capability**                  | **Seedance 2.0**             | **Seedance 2.0 Fast**             | **Seedance 1.5 Pro**             | **Seedance 1.0 Pro**             | **Seedance 1.0 Pro Fast**             | **Seedance 1.0 Lite i2v**             | **Seedance 1.0 Lite t2v**             |
| ------------------------------- | ---------------------------- | --------------------------------- | -------------------------------- | -------------------------------- | ------------------------------------- | ------------------------------------- | ------------------------------------- |
| **Model ID**                    | `doubao-seedance-2-0-260128` | `doubao-seedance-2-0-fast-260128` | `doubao-seedance-1-5-pro-251215` | `doubao-seedance-1-0-pro-250528` | `doubao-seedance-1-0-pro-fast-251015` | `doubao-seedance-1-0-lite-i2v-250428` | `doubao-seedance-1-0-lite-t2v-250428` |
| **Text-to-Video**               | ✅                           | ✅                                | ✅                               | ✅                               | ✅                                    | ✅                                    | ✅                                    |
| **Image-to-Video (first frame)**| ✅                           | ✅                                | ✅                               | ✅                               | ✅                                    | ✅                                    | -                                     |
| **Image-to-Video (first+last)** | ✅                           | ✅                                | ✅                               | ✅                               | -                                     | ✅                                    | -                                     |
| **Multi-modal reference (img/vid)** | ✅                       | ✅                                | -                                | -                                | -                                     | ✅ (image only)                       | -                                     |
| **Edit / Extend video**         | ✅                           | ✅                                | -                                | -                                | -                                     | -                                     | -                                     |
| **Generate audio**              | ✅                           | ✅                                | ✅                               | -                                | -                                     | -                                     | -                                     |
| **Web search enhancement**      | ✅                           | ✅                                | -                                | -                                | -                                     | -                                     | -                                     |
| **Draft mode**                  | -                            | -                                 | ✅                               | -                                | -                                     | -                                     | -                                     |
| **Return last frame**           | ✅                           | ✅                                | ✅                               | ✅                               | ✅                                    | ✅                                    | ✅                                    |
| **Output resolution**           | 480p, 720p                   | 480p, 720p                        | 480p, 720p, 1080p                | 480p, 720p, 1080p                | 480p, 720p, 1080p                     | 480p, 720p, 1080p                     | 480p, 720p, 1080p                     |
| **Output duration (seconds)**   | 4–15                         | 4–15                              | 4–12                             | 2–12                             | 2–12                                  | 2–12                                  | 2–12                                  |
| **Online inference RPM**        | 600                          | 600                               | 600                              | 600                              | 600                                   | 300                                   | 300                                   |
| **Concurrency**                 | 10                           | 10                                | 10                               | 10                               | 10                                    | 5                                     | 5                                     |
| **Offline inference (Flex)**    | -                            | -                                 | ✅ (500B TPD)                    | ✅ (500B TPD)                    | ✅ (500B TPD)                         | ✅ (250B TPD)                         | ✅ (250B TPD)                         |

_(Note: ✅ = supported, - = not supported or not yet available)_

## 2. Getting Started

> **Note**: Before calling the API, make sure the Python SDK is installed: `pip install 'volcengine-python-sdk[ark]'`, and that the `ARK_API_KEY` environment variable is configured.

Video generation is an **asynchronous process**:

1. After a successful call to the create endpoint, the API returns a task ID (`task_id`).
2. Poll the query endpoint until the task status changes to `succeeded` (or use a Webhook to receive notifications).
3. Once the task completes, extract `content.video_url` to download the MP4 file.

### Step 1: Create a Video Generation Task

```python
import os
from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

if __name__ == "__main__":
    resp = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128",
        content=[
            {
                "type": "text",
                "text": "A girl holds a fox; the girl opens her eyes and looks gently at the camera; the fox cuddles up friendly; the camera slowly pulls back; the girl's hair sways in the wind and you can hear the wind"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png"
                }
            }
        ],
        generate_audio=True,
        ratio="adaptive",
        duration=5,
        watermark=False,
    )
    print(f"Task Created: {resp.id}")
```

### Step 2: Query Task Status

```python
import os
from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

if __name__ == "__main__":
    # Replace with the ID returned when the task was created
    resp = client.content_generation.tasks.get(task_id="cgt-2025****")
    print(resp)

    if resp.status == "succeeded":
        print(f"Video URL: {resp.content.video_url}")
```

## 3. Practical Scenarios (Python)

### 3.1 Text-to-Video

Generate a video based on a user-provided prompt. Results have a degree of randomness, making this useful for creative inspiration.

```python
import os
import time
from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

create_result = client.content_generation.tasks.create(
    model="doubao-seedance-2-0-260128",
    content=[
        {
            "type": "text",
            "text": "Realistic style, under a clear blue sky, a vast field of white daisies; the camera slowly zooms in until a close-up of a single daisy fills the frame, a few glistening dew drops on its petals"
        }
    ],
    ratio="16:9",
    duration=5,
    watermark=True,
)

# Poll for result
task_id = create_result.id
while True:
    get_result = client.content_generation.tasks.get(task_id=task_id)
    if get_result.status == "succeeded":
        print(f"Task succeeded! Video download URL: {get_result.content.video_url}")
        break
    elif get_result.status == "failed":
        print(f"Task failed: {get_result.error}")
        break
    else:
        print(f"Processing ({get_result.status})... waiting 10 seconds")
        time.sleep(10)
```

### 3.2 Image-to-Video — First Frame

Specify the first frame of the video; the model generates a coherent video based on that image. Setting `generate_audio=True` also generates audio.

```python
# Build the content list
content = [
    {
        "type": "text",
        "text": "A girl holds a fox; the camera slowly pulls back; her hair sways in the wind and you can hear the wind"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png"
        }
    }
]

create_result = client.content_generation.tasks.create(
    model="doubao-seedance-2-0-260128",
    content=content,
    generate_audio=True,  # Enable audio generation
    ratio="adaptive",
    duration=5,
    watermark=True,
)
```

### 3.3 Image-to-Video — First and Last Frame

By specifying both the start and end images of the video, the model generates a smoothly connected video between the two frames.

```python
content = [
    {
        "type": "text",
        "text": "The girl in the image says 'cheese' to the camera, 360-degree orbit shot"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_first_frame.jpeg"
        },
        "role": "first_frame"  # Specify role as first frame
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_last_frame.jpeg"
        },
        "role": "last_frame"  # Specify role as last frame
    }
]

create_result = client.content_generation.tasks.create(
    model="doubao-seedance-2-0-260128",
    content=content,
    ratio="adaptive",
    duration=5
)
```

### 3.4 Image-to-Video — Reference Images

The model accurately extracts key visual features of objects from reference images (1–4 images supported) and uses those features to faithfully reproduce the object's shape, color, and texture in the generated video, ensuring the video visually matches the reference images.

```python
content = [
    {
        "type": "text",
        "text": "[Image1] A boy wearing glasses and a blue T-shirt and [Image2] a corgi puppy, sitting on the lawn from [Image3], cartoon style video"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_1.png"
        },
        "role": "reference_image"  # Specify as reference image
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_2.png"
        },
        "role": "reference_image"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_3.png"
        },
        "role": "reference_image"
    }
]

create_result = client.content_generation.tasks.create(
    # Note: select a model that supports this feature, e.g. Seedance 1.0 lite i2v
    model="doubao-seedance-1-0-lite-i2v-250428",
    content=content,
    ratio="16:9",
    duration=5
)
```

### 3.5 Video Task Management

**List tasks:**

```python
resp = client.content_generation.tasks.list(
    page_size=3,
    status="succeeded",
)
print(resp)
```

**Delete or cancel a task:**

```python
client.content_generation.tasks.delete(task_id="cgt-2025****")
```

## 4. Prompt Tips

To get higher-quality results that match expectations, follow these prompt writing principles:

- **Core formula: prompt = subject + motion + background + motion + camera + motion ...**
- **Be direct and precise**: use concise, accurate natural language to describe the desired effect; replace abstract descriptions with concrete ones.
- **Step-by-step strategy**: if you have a specific expected result, first generate a matching image with an image generation model, then use **image-to-video** to generate the video clip.
- **Prioritize important content**: remove unimportant details and put key content first.
- **Embrace randomness**: pure text-to-video has a high degree of randomness, making it ideal for creative inspiration.
- **Input quality**: for image-to-video, upload the highest-quality images you can — the quality of the input image has a huge impact on the final video result.

## 5. Advanced Features

### 5.1 Output Specification Parameters (Request Body Controls)

In strict validation mode, pass the following parameters directly in the Request Body to control video specs:

| **Parameter**  | **Description**       | **Example Values**                                     |
| -------------- | --------------------- | ------------------------------------------------------ |
| `resolution`   | Output resolution     | `480p`, `720p`, `1080p`                                |
| `ratio`        | Aspect ratio          | `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9`, `adaptive` |
| `duration`     | Duration (seconds)    | Integer, e.g. `5`                                      |
| `frames`       | Number of frames      | Prefer `duration`. If using `frames`, must satisfy `25 + 4n` |
| `seed`         | Random seed           | Integer, used to reproduce generation results          |
| `camera_fixed` | Lock camera           | `true` or `false`                                      |
| `watermark`    | Include watermark     | `true` or `false`                                      |

### 5.2 Offline Inference (Flex Tier)

For non-real-time scenarios, setting `service_tier="flex"` reduces the call price by 50%.

```python
create_result = client.content_generation.tasks.create(
    model="doubao-seedance-1-5-pro-251215",
    content=[...],  # omitted
    service_tier="flex",             # Enable offline inference
    execution_expires_after=172800,  # Set task timeout
)
```

### 5.3 Draft Mode

Draft mode helps validate prompt intent and camera direction at low cost. (_Note: currently only supported by Seedance 1.5 Pro_)

**Step 1: Generate a low-cost draft**

```python
create_result = client.content_generation.tasks.create(
    model="doubao-seedance-1-5-pro-251215",
    content=[...],
    seed=20,
    duration=6,
    draft=True  # Enable draft mode
)
# Get the returned draft_task_id: "cgt-2026****-pzjqb"
```

**Step 2: Generate the final video based on the draft**

Once satisfied with the draft, use the draft task ID to generate the full high-quality version:

```python
create_result = client.content_generation.tasks.create(
    model="doubao-seedance-1-5-pro-251215",
    content=[
        {
            "type": "draft_task",
            "draft_task": {"id": "cgt-2026****-pzjqb"}  # Reference the draft task
        }
    ],
    resolution="720p",
    watermark=False
)
```

### 5.4 Webhook Status Callback

By setting `callback_url`, you can avoid the resource cost of polling. Below is a simple Flask service example for receiving Ark Webhooks:

```python
from flask import Flask, request, jsonify
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

@app.route('/webhook/callback', methods=['POST'])
def video_task_callback():
    callback_data = request.get_json()
    if not callback_data:
        return jsonify({"code": 400, "msg": "Invalid data"}), 400

    task_id = callback_data.get('id')
    status = callback_data.get('status')

    logging.info(f"Task Callback | ID: {task_id} | Status: {status}")

    if status == 'succeeded':
        # Trigger business logic here, e.g. save to DB or fetch content via API
        pass

    return jsonify({"code": 200, "msg": "Success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

## 6. Usage Limits and Cropping Rules

### 6.1 Multi-modal Input Limits

- **Images**: single image < 30 MB. Supports jpeg, png, webp, etc. Aspect ratio between (0.4, 2.5); dimensions 300–6000 px.
- **Videos**: single video < 50 MB. Supports mp4, mov. Duration 2–15 seconds. Frame rate 24–60 FPS.
- **Audio**: single audio file < 15 MB. Supports wav, mp3. Duration 2–15 seconds.

### 6.2 Automatic Image Cropping Rules

When the specified `ratio` (aspect ratio) does not match the aspect ratio of the input image, the service applies **center-crop** logic:

1. If the original image is "taller and narrower" than the target (original ratio < target ratio), **width is used as the reference** and the image is cropped top and bottom, centered.
2. If the original image is "wider and flatter" than the target (original ratio > target ratio), **height is used as the reference** and the image is cropped left and right, centered.

> **Recommendation**: upload high-quality images whose aspect ratio is close to the target `ratio` to get the best results and avoid key subjects being cropped.

### 6.3 Task Lifecycle

Task data (such as status and video download links) is **retained for 24 hours only** and will be automatically deleted after that. After confirming success via callback or polling, promptly download and store the output to your own storage (e.g. OSS).
