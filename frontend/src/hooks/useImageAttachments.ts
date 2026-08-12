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
  return {
    data: image.dataUrl.split(",")[1] ?? "",
    media_type: image.mimeType,
  };
}

export function useImageAttachments(initialImages: AttachedImage[] = []) {
  const { t } = useTranslation("dashboard");
  const [images, setImages] = useState<AttachedImage[]>(initialImages);
  const [error, setError] = useState<string | null>(null);
  const generationRef = useRef(0);

  useEffect(() => () => {
    generationRef.current += 1;
  }, []);

  const addFiles = useCallback((files: File[]) => {
    setError(null);
    const generation = generationRef.current;
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      if (file.size > MAX_IMAGE_BYTES) {
        setError(t("image_too_large_hint", { name: file.name }));
        continue;
      }
      const reader = new FileReader();
      reader.onload = (event) => {
        if (generationRef.current !== generation) return;
        const dataUrl = event.target?.result;
        if (typeof dataUrl !== "string") return;
        setImages((current) => {
          if (current.length >= MAX_ATTACHED_IMAGES) return current;
          return [...current, { id: uid(), dataUrl, mimeType: file.type }];
        });
      };
      reader.readAsDataURL(file);
    }
  }, [t]);

  const removeImage = useCallback((id: string) => {
    setImages((current) => current.filter((image) => image.id !== id));
    setError(null);
  }, []);

  const resetImages = useCallback(() => {
    generationRef.current += 1;
    setImages([]);
    setError(null);
  }, []);

  const invalidatePendingReaders = useCallback(() => {
    generationRef.current += 1;
  }, []);

  return { images, error, addFiles, removeImage, resetImages, invalidatePendingReaders };
}
