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

  useEffect(() => () => {
    generationRef.current += 1;
  }, []);

  const addFiles = useCallback((files: File[]) => {
    setError(null);
    const generation = generationRef.current;
    const imageFiles = files.filter((file) => file.type.startsWith("image/"));
    const remainingCapacity = MAX_ATTACHED_IMAGES - images.length;
    if (imageFiles.length > remainingCapacity) {
      setError(t("max_images_hint", { count: MAX_ATTACHED_IMAGES }));
    }
    for (const file of imageFiles.slice(0, remainingCapacity)) {
      if (file.size > MAX_IMAGE_BYTES) {
        setError(t("image_too_large_hint", { name: file.name }));
        continue;
      }
      const reader = new FileReader();
      setPendingReads((current) => current + 1);
      const finishRead = () => {
        if (generationRef.current !== generation) return;
        setPendingReads((current) => Math.max(0, current - 1));
      };
      reader.onload = (event) => {
        if (generationRef.current !== generation) return;
        const dataUrl = event.target?.result;
        if (typeof dataUrl === "string") {
          setImages((current) => {
            if (current.length >= MAX_ATTACHED_IMAGES) return current;
            return [...current, { id: uid(), dataUrl, mimeType: file.type }];
          });
        }
        finishRead();
      };
      reader.onerror = finishRead;
      reader.onabort = finishRead;
      reader.readAsDataURL(file);
    }
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
  }, []);

  const invalidatePendingReaders = useCallback(() => {
    generationRef.current += 1;
    setPendingReads(0);
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
