import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ImagePayload } from "@/types";
import { uid } from "@/utils/id";
import {
  dataUrlByteLength,
  MAX_ENCODED_IMAGE_BYTES,
  MAX_IMAGE_FILE_BYTES,
  TRANSCODED_IMAGE_MIME_TYPE,
  transcodeImageToJpeg,
  type TranscodeResult,
} from "@/utils/image-transcode";

export const MAX_ATTACHED_IMAGES = 5;

/** Transcoding failure reasons mapped to copy; `gif` shares the file-type gate GIF notice. */
const TRANSCODE_FAILURE_HINTS = {
  oversized: "image_still_too_large_hint",
  gif: "image_gif_unsupported_hint",
  decode: "image_unreadable_hint",
} as const;

const HISTORICAL_TRANSCODE_FAILURE_HINTS = {
  oversized: "message_edit_historical_attachment_oversized_hint",
  gif: "message_edit_historical_attachment_gif_hint",
  decode: "message_edit_historical_attachment_unreadable_hint",
} as const;

export interface AttachedImage {
  id: string;
  dataUrl: string;
  mimeType: string;
}

interface PendingAppendTranscode {
  kind: "append";
  file: File;
  generation: number;
}

interface InitialTranscode {
  kind: "initial";
  imageId: string;
  dataUrl: string;
  mimeType: string;
  index: number;
}

interface PendingInitialTranscode extends InitialTranscode {
  generation: number;
}

type PendingTranscode = PendingAppendTranscode | PendingInitialTranscode;

function prepareInitialImages(initialImages: AttachedImage[] | (() => AttachedImage[])) {
  const images = typeof initialImages === "function" ? initialImages() : initialImages;
  const pending: InitialTranscode[] = [];
  images.forEach((image, index) => {
    if (dataUrlByteLength(image.dataUrl) <= MAX_ENCODED_IMAGE_BYTES) return;
    pending.push({
      kind: "initial",
      imageId: image.id,
      dataUrl: image.dataUrl,
      mimeType: image.mimeType,
      index,
    });
  });
  return { images, pending };
}

function dataUrlToFile(pending: PendingInitialTranscode): File {
  const separatorIndex = pending.dataUrl.indexOf(",");
  if (separatorIndex < 0) throw new Error("invalid image data URL");
  const binary = atob(pending.dataUrl.slice(separatorIndex + 1));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], `historical-attachment-${pending.index + 1}`, { type: pending.mimeType });
}

export function imagePayloadToAttachment(image: ImagePayload): AttachedImage {
  return {
    id: uid(),
    dataUrl: `data:${image.media_type};base64,${image.data}`,
    mimeType: image.media_type,
  };
}

export function attachmentToImagePayload(image: AttachedImage): ImagePayload {
  const separatorIndex = image.dataUrl.indexOf(",");
  return {
    data: separatorIndex >= 0 ? image.dataUrl.slice(separatorIndex + 1) : "",
    media_type: image.mimeType,
  };
}

export function useImageAttachments(initialImages: AttachedImage[] | (() => AttachedImage[]) = []) {
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  const [initialState] = useState(() => prepareInitialImages(initialImages));
  const [images, setImages] = useState<AttachedImage[]>(initialState.images);
  const [error, setError] = useState<string | null>(null);
  const [pendingTranscodes, setPendingTranscodes] = useState(initialState.pending.length);
  const generationRef = useRef(0);
  const pendingSlotsRef = useRef(0);
  const queueRef = useRef<PendingTranscode[]>([]);
  const transcodingRef = useRef(false);
  const initialQueuedGenerationRef = useRef<number | null>(null);
  const processNextRef = useRef<() => void>(() => {});

  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const processNext = useCallback(() => {
    if (transcodingRef.current) return;
    const pending = queueRef.current.shift();
    if (!pending) return;
    transcodingRef.current = true;

    void (async () => {
      let result: TranscodeResult;
      try {
        const file = pending.kind === "append" ? pending.file : dataUrlToFile(pending);
        result = await transcodeImageToJpeg(file);
      } catch {
        result = { failure: "decode" };
      }

      if (generationRef.current === pending.generation) {
        if ("dataUrl" in result) {
          if (pending.kind === "append") {
            setImages((current) => {
              if (current.length >= MAX_ATTACHED_IMAGES) return current;
              return [
                ...current,
                { id: uid(), dataUrl: result.dataUrl, mimeType: TRANSCODED_IMAGE_MIME_TYPE },
              ];
            });
          } else {
            setImages((current) => current.map((image) => (
              image.id === pending.imageId
                ? { ...image, dataUrl: result.dataUrl, mimeType: TRANSCODED_IMAGE_MIME_TYPE }
                : image
            )));
          }
        } else if (pending.kind === "append") {
          setError(tRef.current(TRANSCODE_FAILURE_HINTS[result.failure], { name: pending.file.name }));
        } else {
          setImages((current) => current.filter((image) => image.id !== pending.imageId));
          setError(tRef.current(HISTORICAL_TRANSCODE_FAILURE_HINTS[result.failure], {
            index: pending.index + 1,
          }));
        }
        if (pending.kind === "append") {
          pendingSlotsRef.current = Math.max(0, pendingSlotsRef.current - 1);
        }
        setPendingTranscodes((current) => Math.max(0, current - 1));
      }
      transcodingRef.current = false;
      processNextRef.current();
    })();
  }, []);

  useEffect(() => {
    processNextRef.current = processNext;
  }, [processNext]);

  // Over-budget initial attachments enter transcoding synchronously, then join the same serial queue after mount.
  useEffect(() => {
    const generation = generationRef.current;
    if (initialState.pending.length > 0 && initialQueuedGenerationRef.current !== generation) {
      initialQueuedGenerationRef.current = generation;
      queueRef.current.push(...initialState.pending.map((pending) => ({ ...pending, generation })));
      processNext();
    }
    return () => {
      // Nobody receives queued decodes after unmount; clear queues so closed panels stop consuming decoding/memory.
      generationRef.current += 1;
      queueRef.current = [];
    };
  }, [initialState.pending, processNext]);

  const addFiles = useCallback((files: File[]) => {
    setError(null);
    const generation = generationRef.current;
    const imageFiles = files.filter((file) => {
      if (!file.type.startsWith("image/")) return false;
      // GIF never enters canvas: reencoding silently retains first frames only; unchanged uploads would
      // uniquely bypass per-image budgets. Reject explicitly, asking users to convert PNG/JPEG themselves.
      if (file.type === "image/gif") {
        setError(t("image_gif_unsupported_hint", { name: file.name }));
        return false;
      }
      if (file.size <= MAX_IMAGE_FILE_BYTES) return true;
      setError(t("image_too_large_hint", { name: file.name }));
      return false;
    });
    const remainingCapacity = Math.max(
      0,
      MAX_ATTACHED_IMAGES - images.length - pendingSlotsRef.current,
    );
    if (imageFiles.length > remainingCapacity) {
      setError(t("max_images_hint", { count: MAX_ATTACHED_IMAGES }));
    }
    const filesToTranscode = imageFiles.slice(0, remainingCapacity);
    pendingSlotsRef.current += filesToTranscode.length;
    setPendingTranscodes((current) => current + filesToTranscode.length);
    queueRef.current.push(...filesToTranscode.map((file) => ({ kind: "append" as const, file, generation })));

    // Decode serially; simultaneous large images exhaust mobile memory.
    processNext();
  }, [images.length, processNext, t]);

  // Returned input images already transcoded for sends; append unchanged, retaining all over-limit images for user selection.
  const appendImages = useCallback((appended: AttachedImage[]) => {
    if (appended.length > 0) setImages((current) => [...current, ...appended]);
  }, []);

  const removeImage = useCallback((id: string) => {
    setImages((current) => current.filter((image) => image.id !== id));
    setError(null);
  }, []);

  // Accepted sends remove sent images only, preserving attachments added midflight.
  const removeImages = useCallback((ids: string[]) => {
    const removed = new Set(ids);
    setImages((current) => current.filter((image) => !removed.has(image.id)));
    setError(null);
  }, []);

  const invalidatePendingTranscodes = useCallback(() => {
    generationRef.current += 1;
    setPendingTranscodes(0);
    pendingSlotsRef.current = 0;
    queueRef.current = [];
  }, []);

  return {
    images,
    error,
    isReading: pendingTranscodes > 0,
    addFiles,
    appendImages,
    removeImage,
    removeImages,
    invalidatePendingTranscodes,
  };
}
