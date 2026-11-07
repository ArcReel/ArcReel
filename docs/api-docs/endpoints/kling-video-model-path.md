# kling-video-model-path

Text-to-video and image-to-video through the newer Kling API, with model names in paths and API Key authentication only.

- Protocol: [API update announcements](https://klingai.com/document-api/updates/api) entries dated 2026.06.17, 2026.07.15, 2026.10.28 and 2026.11.04; [Kling 4.0 text-to-video](https://klingai.com/document-api/api/video/4-0/text-to-video) and [Image-to-video](https://klingai.com/document-api/api/video/4-0/image-to-video), [Kling 4.0 Flash text-to-video](https://klingai.com/document-api/api/video/4-0-flash/text-to-video) and [Image-to-video](https://klingai.com/document-api/api/video/4-0-flash/image-to-video), [Kling 3.0 Turbo text-to-video](https://klingai.com/document-api/api/video/3-0-turbo/text-to-video) and [Image-to-video](https://klingai.com/document-api/api/video/3-0-turbo/image-to-video) sections for creating tasks and querying by task ID
- Authentication and domains: [API authentication](https://klingai.com/document-api/api/get-started/authentication) specifies the service domain and API Key; Access Key / Secret Key apply only to the older API
- Capabilities: [Video capability map](https://klingai.com/document-api/guides/capability-map/video) for first/last frames and joint audio/video generation; [Kling 4.0 video guide](https://klingai.com/document-api/api/usage-guide/kling-4-0) explains that synchronized audio/video is enabled by default without a toggle
- Error codes: [Error codes](https://klingai.com/document-api/api/get-started/error-codes)
- Pricing: [Video pricing (mainland China site)](https://klingai.com/document-api/pricing/base/video) rows for Kling 4.0, Kling 4.0 Flash and Kling 3.0 Turbo; the task-query response field `usage.output_duration`
- Code: `lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["kling-video-model-path"]`, `lib/backends/video_backends/kling_model_path.py::KlingModelPathVideoBackend`, `lib/backends/kling_shared.py::kling_model_path_task`
