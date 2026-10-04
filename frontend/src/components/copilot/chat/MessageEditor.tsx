import { useCallback, useEffect, useRef, useState } from "react";
import { Paperclip, TriangleAlert, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ImagePayload } from "@/types";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { Message, MessageContent } from "@/components/ui/message";
import { Textarea } from "@/components/ui/textarea";
import {
  attachmentToImagePayload,
  imagePayloadToAttachment,
  MAX_ATTACHED_IMAGES,
  useImageAttachments,
} from "@/hooks/useImageAttachments";

// ---------------------------------------------------------------------------
// MessageEditor — 已发送的用户消息原地变成编辑器，仍是同一位置、同一外形的气泡。
// ---------------------------------------------------------------------------

interface MessageEditorProps {
  initialText: string;
  /** 锚点消息带的图片附件，可在改写前逐张移除。 */
  initialImages: ImagePayload[];
  submitting: boolean;
  /** 此刻允许提交改写；false 时保留编辑内容但锁住重新发送。 */
  canSubmit: boolean;
  onCancel: () => void;
  onSubmit: (text: string, images: ImagePayload[]) => void;
}

export function MessageEditor({
  initialText,
  initialImages,
  submitting,
  canSubmit,
  onCancel,
  onSubmit,
}: MessageEditorProps) {
  const { t } = useTranslation("dashboard");
  const [text, setText] = useState(initialText);
  const {
    images,
    error: attachError,
    isReading: isReadingImages,
    addFiles,
    removeImage,
    invalidatePendingTranscodes,
  } = useImageAttachments(() => initialImages.map(imagePayloadToAttachment));
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 部分输入法在组合确认的那次 keydown 上不置 isComposing，靠组合事件补齐
  const isComposingRef = useRef(false);

  // 进入编辑态即聚焦，光标置于末尾
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  const hasContent = Boolean(text.trim()) || images.length > 0;
  const attachDisabled = submitting || isReadingImages || images.length >= MAX_ATTACHED_IMAGES;

  const submit = useCallback(() => {
    if (submitting || !canSubmit || !hasContent || isReadingImages) return;
    invalidatePendingTranscodes();
    onSubmit(text, images.map(attachmentToImagePayload));
  }, [text, images, hasContent, isReadingImages, submitting, canSubmit, onSubmit, invalidatePendingTranscodes]);

  const handlePaste = useCallback((event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (attachDisabled) return;
    const imageFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (imageFiles.length === 0) return;
    event.preventDefault();
    addFiles(imageFiles);
  }, [addFiles, attachDisabled]);

  return (
    <Message align="end">
      <MessageContent>
        <Bubble variant="tinted" align="end" className="w-full max-w-[85%]">
          <BubbleContent className="w-full">
            <div className="flex flex-col gap-2">
              {images.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1.5 pr-1.5">
                  {images.map(({ id, dataUrl }, index) => (
                    <div key={id} className="relative">
                      <img
                        src={dataUrl}
                        alt={t("message_edit_attachment", { index: index + 1, total: images.length })}
                        className="size-14 rounded-md border border-border object-cover"
                      />
                      <Button
                        variant="secondary"
                        size="icon-xs"
                        className="absolute -top-1.5 -right-1.5"
                        disabled={submitting}
                        onClick={() => removeImage(id)}
                        aria-label={t("message_edit_remove_attachment", { index: index + 1, total: images.length })}
                      >
                        <X aria-hidden />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="xs"
                  disabled={attachDisabled}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Paperclip data-icon="inline-start" aria-hidden />
                  {images.length >= MAX_ATTACHED_IMAGES
                    ? t("max_images_hint", { count: MAX_ATTACHED_IMAGES })
                    : t("attach_image")}
                </Button>
                {attachError && <span role="alert" className="text-xs text-destructive">{attachError}</span>}
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept="image/*"
                  aria-label={t("upload_attachment_aria")}
                  className="hidden"
                  disabled={submitting}
                  onChange={(event) => {
                    addFiles(Array.from(event.target.files ?? []));
                    event.target.value = "";
                  }}
                />
              </div>
              <Textarea
                ref={textareaRef}
                value={text}
                rows={2}
                disabled={submitting}
                aria-label={t("message_edit_textarea_label")}
                onChange={(event) => setText(event.target.value)}
                onPaste={handlePaste}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  isComposingRef.current = false;
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    if (submitting) return;
                    // 外壳在紧凑档用 Esc 收起面板，并跳过已被处理的 Esc：这里标记一下，面板不跟着收起
                    event.preventDefault();
                    onCancel();
                  } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    // 组合中的这一下确认的是候选词，不是提交（与主输入框同口径）
                    const native = event.nativeEvent;
                    if (native.isComposing || native.keyCode === 229 || isComposingRef.current) return;
                    event.preventDefault();
                    submit();
                  }
                }}
              />
              <p className="flex items-start gap-1.5 text-xs text-warn">
                <TriangleAlert aria-hidden className="mt-0.5 size-3 shrink-0" />
                {t("message_edit_consequence")}
              </p>
              <div className="flex items-center justify-end gap-2">
                <span className="mr-auto text-xs text-subtle-foreground">{t("message_edit_resend_hint")}</span>
                {/* 改写在途时取消无从撤回请求：关掉编辑器只会让随后的会话切换显得无端 */}
                <Button variant="outline" size="xs" disabled={submitting} onClick={onCancel}>
                  {t("message_edit_cancel")}
                </Button>
                <Button size="xs" disabled={submitting || !canSubmit || !hasContent || isReadingImages} onClick={submit}>
                  {submitting ? t("message_edit_resending") : t("message_edit_resend")}
                </Button>
              </div>
            </div>
          </BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
  );
}
