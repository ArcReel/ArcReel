# gemini-interactions-video

- Protocol: [Gemini Omni](https://ai.google.dev/gemini-api/docs/omni), [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview), [Interactions request and response fields](https://ai.google.dev/api/interactions)
- Model: [Gemini Omni Flash](https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash)
- Pricing: [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["gemini-interactions-video"]`, `lib/backends/video_backends/gemini_interactions.py::GeminiInteractionsVideoBackend`

## Custom providers

This endpoint uses an AI Studio API Key. The Base URL points to a Gemini Interactions API-compatible service; submission and polling paths can be overridden independently. Defaults are `POST /v1beta/interactions` for submission and `GET /v1beta/interactions/{task_id}` for polling. Retrieve the video from inline data in the completed response without a separate download request. Resume by querying the saved interaction ID, not by submitting again.

`gemini-vertex-interactions-video` uses a service account and remains exclusive to the built-in Vertex AI provider. Its credentials are not interchangeable with this endpoint's credentials.

## Required contracts

- Request: `input` images and text, `generation_config.video_config.task`, `response_format` and background polling cover text-to-video, first-frame, first-and-last-frame and reference-image generation; see [Gemini Omni](https://ai.google.dev/gemini-api/docs/omni). The model generates audio; requests have no separate audio toggle and do not support reference audio.
- Retrieval: decode inline `output_video.data` from the completed response. This backend does not yet implement `delivery="uri"`, which the official documentation recommends for larger videos.
- Metering: read output video tokens from the Interactions response, falling back to total output tokens when modality breakdown is absent; see [Interactions request and response fields](https://ai.google.dev/api/interactions). Estimates use the backend's resolution-to-video-token conversion; the pricing page provides its basis.
- Model name: model-name-based prefill covers `gemini-omni-1.2-*` without rewriting the submitted model name; see the [model page](https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash) for routing and version details.

The catalog entry declares reference-image counts and duration ranges. First-and-last-frame interpolation has a narrower duration range than ordinary generation. Catalog duration constraints currently narrow only by resolution or reference images and cannot express first-and-last-frame mode. This backend does not prevalidate that restriction; the upstream rejects out-of-range first-and-last-frame requests.

HTTP test doubles verify only request serialization, retrieval and metering. Real-account availability, server limits and actual usage reports still require validation on the external platform.
