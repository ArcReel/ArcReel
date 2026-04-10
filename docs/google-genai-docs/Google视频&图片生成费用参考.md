## Official Pricing Reference Links

[AI Studio](https://ai.google.dev/gemini-api/docs/pricing.md.txt)
[Vertex AI](https://r.jina.ai/https://cloud.google.com/vertex-ai/generative-ai/pricing)

## 🎬 Veo 3.1 / Veo 3.1 Fast Video Generation Pricing (per second)

> AI Studio and Vertex AI prices are the same

### Veo 3.1 (Standard)

| Resolution   | Audio      | Price ($/s)  | 8s Video Cost |
| ------------ | ---------- | ------------ | ------------ |
| 720p / 1080p | With audio | 0.40         | 3.20          |
| 720p / 1080p | No audio   | 0.20         | 1.60          |
| 4K           | With audio | 0.60         | 4.80          |
| 4K           | No audio   | 0.40         | 3.20          |

------

### Veo 3.1 Fast (Lower Price / Faster)

| Resolution   | Audio      | Price ($/s)  | 8s Video Cost |
| ------------ | ---------- | ------------ | ------------ |
| 720p / 1080p | With audio | 0.15         | 1.20          |
| 720p / 1080p | No audio   | 0.10         | 0.80          |
| 4K           | With audio | 0.35         | 2.80          |
| 4K           | No audio   | 0.30         | 2.40          |

------

## 🖼️ Image Generation Pricing (tokens → per image)

> Token conversion rules (official definition):
> **Cost per image = token count × unit price / 1,000,000**

------

### gemini-3-pro-image-preview

#### AI Studio

##### Input Image (as reference)

| Item     | Token Count | Unit Price     | Cost per Image   |
| -------- | ---------- | -------------- | ---------------- |
| Input image | 560 tokens | $2 / 1M tokens | **$0.0011 / image** |

##### Output Image

| Output Resolution | Token Count | Unit Price       | Cost per Image  |
| ---------- | ----------- | ---------------- | --------------- |
| 1K / 2K    | 1120 tokens | $120 / 1M tokens | **$0.134 / image** |
| 4K         | 2000 tokens | $120 / 1M tokens | **$0.24 / image**  |

#### Vertex AI Standard

##### Input Image (as reference)

| Item     | Token Count | Unit Price                   | Cost per Image   |
| -------- | ---------- | ---------------------------- | ---------------- |
| Input image | 560 tokens | $2 / 1M tokens (≤200K ctx) | **$0.0011 / image** |
| Input image | 560 tokens | $4 / 1M tokens (>200K ctx) | **$0.0022 / image** |

##### Output Image

| Output Resolution | Token Count | Unit Price       | Cost per Image  |
| ---------- | ----------- | ---------------- | --------------- |
| 1K / 2K    | 1120 tokens | $120 / 1M tokens | **$0.134 / image** |
| 4K         | 2000 tokens | $120 / 1M tokens | **$0.24 / image**  |

------

### gemini-3.1-flash-image-preview

#### AI Studio

##### Input Image (as reference)

| Item     | Token Count | Unit Price        | Cost per Image     |
| -------- | ----------- | ----------------- | ------------------ |
| Input image | 1120 tokens | $0.25 / 1M tokens | **$0.00028 / image**  |

##### Output Image

| Output Resolution | Token Count | Unit Price      | Cost per Image   |
| ---------- | ----------- | --------------- | ---------------- |
| 512px      | 747 tokens  | $60 / 1M tokens | **$0.045 / image**  |
| 1K         | 1120 tokens | $60 / 1M tokens | **$0.067 / image**  |
| 2K         | 1680 tokens | $60 / 1M tokens | **$0.101 / image**  |
| 4K         | 2520 tokens | $60 / 1M tokens | **$0.151 / image**  |

#### Vertex AI Standard

##### Input Image (as reference)

| Item     | Token Count | Unit Price        | Cost per Image     |
| -------- | ----------- | ----------------- | ------------------ |
| Input image | 1120 tokens | $0.50 / 1M tokens | **$0.00056 / image**  |

##### Output Image

| Output Resolution | Token Count | Unit Price      | Cost per Image   |
| ---------- | ----------- | --------------- | ---------------- |
| 512px      | 747 tokens  | $60 / 1M tokens | **$0.045 / image**  |
| 1K         | 1120 tokens | $60 / 1M tokens | **$0.067 / image**  |
| 2K         | 1680 tokens | $60 / 1M tokens | **$0.101 / image**  |
| 4K         | 2520 tokens | $60 / 1M tokens | **$0.151 / image**  |

------

## 📊 Price Comparison Overview

### Output Image Cost per Image Comparison

| Model | Resolution | AI Studio | Vertex Standard |
| ---- | ------ | --------- | --------------- |
| gemini-3-pro | 1K/2K | $0.134 | $0.134 |
| gemini-3-pro | 4K | $0.24 | $0.24 |
| gemini-3.1-flash | 512px | $0.045 | $0.045 |
| gemini-3.1-flash | 1K | $0.067 | $0.067 |
| gemini-3.1-flash | 2K | $0.101 | $0.101 |
| gemini-3.1-flash | 4K | $0.151 | $0.151 |

> The gemini-3.1-flash 2K image cost is approximately **75%** of gemini-3-pro ($0.101 vs $0.134)
