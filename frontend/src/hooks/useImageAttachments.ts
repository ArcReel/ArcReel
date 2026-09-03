import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ImagePayload } from "@/types";
import { uid } from "@/utils/id";
import {
  MAX_IMAGE_FILE_BYTES,
  TRANSCODED_IMAGE_MIME_TYPE,
  transcodeImageToJpeg,
} from "@/utils/image-transcode";

export const MAX_ATTACHED_IMAGES = 5;

export interface AttachedImage {
  id: string;
  dataUrl: string;
  mimeType: string;
}

interface PendingTranscode {
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
  const [pendingTranscodes, setPendingTranscodes] = useState(0);
  const generationRef = useRef(0);
  const pendingSlotsRef = useRef(0);
  const queueRef = useRef<PendingTranscode[]>([]);
  const transcodingRef = useRef(false);

  useEffect(() => () => {
    generationRef.current += 1;
  }, []);

  const addFiles = useCallback((files: File[]) => {
    setError(null);
    const generation = generationRef.current;
    const imageFiles = files.filter((file) => {
      if (!file.type.startsWith("image/")) return false;
      // GIF 不进 canvas：重编码只会留下首帧，动画内容被静默丢弃；原样上传又会让它
      // 成为唯一绕开单张预算的格式。明确拒绝，让用户自己转成 PNG / JPEG。
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
    queueRef.current.push(...filesToTranscode.map((file) => ({ file, generation })));

    // 逐张串行：同时解码多张大图会在移动端撑爆内存。
    const processNext = () => {
      if (transcodingRef.current) return;
      const pending = queueRef.current.shift();
      if (!pending) return;
      transcodingRef.current = true;
      void transcodeImageToJpeg(pending.file).then((result) => {
        if (generationRef.current === pending.generation) {
          if ("dataUrl" in result) {
            setImages((current) => {
              if (current.length >= MAX_ATTACHED_IMAGES) return current;
              return [
                ...current,
                { id: uid(), dataUrl: result.dataUrl, mimeType: TRANSCODED_IMAGE_MIME_TYPE },
              ];
            });
          } else {
            setError(t(
              result.failure === "oversized" ? "image_still_too_large_hint" : "image_unreadable_hint",
              { name: pending.file.name },
            ));
          }
          pendingSlotsRef.current = Math.max(0, pendingSlotsRef.current - 1);
          setPendingTranscodes((current) => Math.max(0, current - 1));
        }
        transcodingRef.current = false;
        processNext();
      });
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
    setPendingTranscodes(0);
    pendingSlotsRef.current = 0;
    queueRef.current = [];
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
    removeImage,
    resetImages,
    invalidatePendingTranscodes,
  };
}
