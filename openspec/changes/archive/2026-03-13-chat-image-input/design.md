## Context

The AgentCopilot conversation panel currently only supports plain text input. The Claude Agent SDK's `ClaudeSDKClient.query()` signature is `str | AsyncIterable[dict]`; the AsyncIterable path supports passing complete multimodal content (text + image blocks). The backend's `send_message` currently only goes through the `str` path; the Service / SessionManager / Router three layers are all unaware of images.

## Goals / Non-Goals

**Goals:**
- Users can attach images in the conversation panel via paste (Ctrl+V), click to upload, and drag-and-drop
- Images are sent to the Agent inline as base64 along with the message
- Image bubbles and history playback in the sent conversation are both correctly rendered

**Non-Goals:**
- Image URL references (requires the backend to additionally fetch; not in this iteration)
- Server-side image compression or format conversion
- Full-screen preview lightbox for images

## Decisions

### Decision 1: Image Transfer Method — Base64 inline in JSON

**Choice**: Image base64 directly embedded in `SendMessageRequest.images[]`, sent in a single request.

**Alternative**: POST multipart upload first to get a temporary ID, then reference it in the message.

**Reason**: No new upload interface needed; minimal frontend and backend changes; single-request stateless approach; no temporary file lifecycle management needed. Maximum 5 images × 5MB; JSON body maximum ~33MB; acceptable.

---

### Decision 2: SDK Integration Layer — Service Layer Encapsulation

**Choice**: `AssistantService.send_message()` is responsible for assembling `content + images` into an AsyncGenerator, passing it to SessionManager; SessionManager only understands `str | AsyncIterable[dict]` and does not understand image structure.

**Alternative A**: Router layer serialization (outermost layer handles it).
**Alternative B**: SessionManager internal handling (innermost layer).

**Reason**: Service is the business logic layer, Router handles HTTP boundary validation, SessionManager handles SDK communication management — clear responsibility layers. Service centralizes content formatting; easier to test and extend in the future.

---

### Decision 3: Separation of echo_text and sdk_prompt

**Choice**: `SessionManager.send_message()` adds an `echo_text` parameter for user bubble display; the `prompt` parameter receives the sdk_prompt (can be str or AsyncGenerator).

**Reason**: User bubble only needs to display the text part; AsyncGenerator cannot be consumed twice, so it cannot be used both for the SDK and for building the echo. Separating the two avoids coupling.

## Risks / Trade-offs

- **JSON body size**: 5 images × 5MB base64 encoded is approximately 33MB. File size validation must be done on the frontend (≤ 5MB/image) to avoid 413 errors. It is also recommended to configure `max_body_size` on the FastAPI backend.
  → Mitigation: frontend interception + backend limit as a double safety net.

- **echo message contains image base64**: `_build_user_echo_message` stores complete base64 in the message_buffer, increasing memory usage.
  → Mitigation: the buffer itself has a prune mechanism; and echo messages are deduplicated and cleared after transcript confirmation.

- **normalize_block passthrough for image blocks**: `turn_schema.normalize_block` does a deepcopy passthrough for unknown types; data is not lost. But if a type allowlist is added in the future, `"image"` needs to be added at the same time.

## Migration Plan

Pure additions; no data migration. Both frontend and backend can be independently deployed in a backward-compatible manner:
- Old frontend does not send the `images` field → backend `images` defaults to empty list, original path is taken
- New frontend sends `images` → requires new backend support; needs simultaneous deployment

## Open Questions

- Is it necessary to explicitly register the `"image"` type in `normalize_block` (add `elif block_type == "image": pass`), or can we rely on the existing deepcopy passthrough? Explicit registration is recommended for improved readability, but does not affect functionality.
