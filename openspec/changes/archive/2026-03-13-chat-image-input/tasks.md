## 1. Backend API Layer

- [x] 1.1 Add `ImageAttachment` Pydantic model in `server/routers/assistant.py` (`data: str`, `media_type: str`)
- [x] 1.2 Add `images: list[ImageAttachment] = Field(default_factory=list, max_length=5)` to `SendMessageRequest`
- [x] 1.3 Route `send_message` passes `req.images` through to `service.send_message()`

## 2. Backend Service Layer

- [x] 2.1 Add `images` parameter (default `None`) to `send_message` signature in `server/agent_runtime/service.py`
- [x] 2.2 Implement `_build_multimodal_prompt(text, images)` async generator, constructing SDK message dicts containing text + image blocks
- [x] 2.3 When images are present, call `_build_multimodal_prompt` to get an async generator; when no images, still pass str

## 3. Backend SessionManager Layer

- [x] 3.1 In `session_manager.py`'s `send_message` signature, change `content: str` to `prompt: str | AsyncIterable[dict]`, add `echo_text: str | None = None` parameter
- [x] 3.2 Change echo logic to use `echo_text or (prompt if isinstance(prompt, str) else "")` as the bubble display text
- [x] 3.3 `_build_user_echo_message` supports passing in a list of content blocks (including image blocks) so that the immediate bubble can also display images

## 4. Backend Normalization Layer

- [x] 4.1 In `server/agent_runtime/turn_schema.py`'s `normalize_block`, explicitly add `elif block_type == "image": pass` branch to indicate that image blocks are intentionally passed through

## 5. Frontend Types

- [x] 5.1 Add `"image"` to the `ContentBlock` type union in `frontend/src/types/assistant.ts`
- [x] 5.2 Add `source?: { type: "base64"; media_type: string; data: string }` field to the `ContentBlock` interface

## 6. Frontend Rendering

- [x] 6.1 Add `case "image"` branch in `ContentBlockRenderer.tsx`, rendering `<img src="data:..." className="max-w-full max-h-64 rounded-lg mt-1" />`

## 7. Frontend Hook

- [x] 7.1 Add `images?: AttachedImage[]` parameter to `sendMessage` signature in `useAssistantSession`
- [x] 7.2 Assemble request body: map `images` to `{ data: dataUrl.split(",")[1], media_type: mimeType }` array

## 8. Frontend AgentCopilot UI

- [x] 8.1 Define `AttachedImage` interface (`id`, `dataUrl`, `mimeType`); add `attachedImages` state
- [x] 8.2 Implement `handlePaste`: read `image/*` items from `ClipboardEvent`, convert to base64 and add to attachment list
- [x] 8.3 Implement `handleDrop` + `handleDragOver`: read images from `DataTransfer.files`, with drag-in highlight effect
- [x] 8.4 Implement `handleFileSelect`: onChange for hidden `<input type="file" multiple accept="image/*">`
- [x] 8.5 Add a 📎 button next to the input box (triggers file input), bind `onPaste`, `onDrop`, `onDragOver` to the input area
- [x] 8.6 Implement thumbnail bar: when `attachedImages` is non-empty, render 64×64 thumbnails above the textarea with an × remove button in the upper right
- [x] 8.7 Disable attachment button when more than 5 images; display error message and reject addition when a single image is > 5MB
- [x] 8.8 `handleSend` calls `sendMessage(text, attachedImages)`; execute `setAttachedImages([])` after sending

## 9. Image Zoom View (Lightbox)

- [x] 9.1 Create `ImageLightbox.tsx` component: full-screen overlay displaying the original image; click overlay or press Esc to close
- [x] 9.2 In `ContentBlockRenderer.tsx`'s `case "image"`, add `cursor-pointer` to the image; click triggers lightbox
- [x] 9.3 On attachment thumbnails in `AgentCopilot.tsx`, click triggers the same lightbox (shared component)
