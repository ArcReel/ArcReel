import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ImagePayload } from "@/types";
import { uid } from "@/utils/id";

export const MAX_ATTACHED_IMAGES = 5;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export interface AttachedImage {
  id: string;
  dataUrl: string;
  mimeType: string;
}

interface PendingImageRead {
  file: File;
  generation: number;
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
  const [images, setImages] = useState<AttachedImage[]>(initialImages);
  const [error, setError] = useState<string | null>(null);
  const [pendingReads, setPendingReads] = useState(0);
  const generationRef = useRef(0);
  const pendingSlotsRef = useRef(0);
  const readQueueRef = useRef<PendingImageRead[]>([]);
  const readingRef = useRef(false);

  useEffect(() => () => {
    generationRef.current += 1;
  }, []);

  const addFiles = useCallback((files: File[]) => {
    setError(null);
    const generation = generationRef.current;
    const imageFiles = files.filter((file) => {
      if (!file.type.startsWith("image/")) return false;
      if (file.size <= MAX_IMAGE_BYTES) return true;
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
    const filesToRead = imageFiles.slice(0, remainingCapacity);
    pendingSlotsRef.current += filesToRead.length;
    setPendingReads((current) => current + filesToRead.length);
    readQueueRef.current.push(...filesToRead.map((file) => ({ file, generation })));

    const processNext = () => {
      if (readingRef.current) return;
      const pending = readQueueRef.current.shift();
      if (!pending) return;
      readingRef.current = true;
      const reader = new FileReader();
      const finishRead = () => {
        if (generationRef.current === pending.generation) {
          pendingSlotsRef.current = Math.max(0, pendingSlotsRef.current - 1);
          setPendingReads((current) => Math.max(0, current - 1));
        }
        readingRef.current = false;
        processNext();
      };
      reader.onload = (event) => {
        if (generationRef.current !== pending.generation) {
          finishRead();
          return;
        }
        const dataUrl = event.target?.result;
        if (typeof dataUrl === "string") {
          setImages((current) => {
            if (current.length >= MAX_ATTACHED_IMAGES) return current;
            return [...current, { id: uid(), dataUrl, mimeType: pending.file.type }];
          });
        }
        finishRead();
      };
      reader.onerror = finishRead;
      reader.onabort = finishRead;
      reader.readAsDataURL(pending.file);
    };
    processNext();
  }, [images.length, t]);

  const removeImage = useCallback((id: string) => {
    setImages((current) => current.filter((image) => image.id !== id));
    setError(null);
  }, []);

  const resetImages = useCallback(() => {
    generationRef.current += 1;
    setImages([]);
    setError(null);
    setPendingReads(0);
    pendingSlotsRef.current = 0;
    readQueueRef.current = [];
  }, []);

  const invalidatePendingReaders = useCallback(() => {
    generationRef.current += 1;
    setPendingReads(0);
    pendingSlotsRef.current = 0;
    readQueueRef.current = [];
  }, []);

  return {
    images,
    error,
    isReading: pendingReads > 0,
    addFiles,
    removeImage,
    resetImages,
    invalidatePendingReaders,
  };
}
