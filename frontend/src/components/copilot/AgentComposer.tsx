import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { ArrowUp, Loader2, Paperclip, Square, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
  AttachmentTrigger,
} from "@/components/ui/attachment";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupTextarea } from "@/components/ui/input-group";
import {
  imagePayloadToAttachment,
  MAX_ATTACHED_IMAGES,
  useImageAttachments,
  type AttachedImage,
} from "@/hooks/useImageAttachments";
import { useAssistantStore } from "@/stores/assistant-store";
import type { ContentBlock, QueuedMessageWithdrawal } from "@/types";
import { voidCall } from "@/utils/async";
import { turnImageAttachments, turnPlainText } from "./chat/utils";
import { QueuedMessageTray } from "./QueuedMessageTray";
import { SlashCommandMenu, useSlashCommands } from "./SlashCommandMenu";

export interface AgentComposerHandle {
  /** Resend input contents (Retry after startup failures). */
  send: () => void;
}

interface AgentComposerProps {
  ref?: Ref<AgentComposerHandle>;
  /** Lock inputs while awaiting answers or pending send requests. */
  disabled: boolean;
  /** Running with empty inputs: replace Send with Stop; nonempty inputs still Send. */
  running: boolean;
  /** Pending send requests: Send buttons spin. */
  sending: boolean;
  placeholder: string;
  /** Hide inputs while questions occupy their positions; keep mounted to retain text/attachments. Queued-message trays remain visible. */
  hidden?: boolean;
  /** Send messages; clear submitted text/attachments after acceptance, retaining unaccepted messages for retries. */
  onSend: (text: string, images?: AttachedImage[]) => Promise<boolean>;
  onInterrupt: () => void;
  /** Edit/delete queued messages; successful edit recalls return contents through store composerAppends. */
  onWithdrawQueued: (id: string, intent: QueuedMessageWithdrawal) => Promise<void>;
  /** Resend Unsent messages. */
  onResendQueued: (id: string) => Promise<void>;
  /** Immediately send queued messages. */
  onSendQueuedNow: (id: string) => Promise<void>;
}

/** Text immediately left of carets starting with / without spaces yet: return slash positions and following filters. */
function findSlashToken(value: string, cursor: number): { pos: number; filter: string } | null {
  const before = value.slice(0, cursor);
  const pos = before.lastIndexOf("/");
  if (pos < 0) return null;
  const atBoundary = pos === 0 || /\s/.test(before[pos - 1]);
  const filter = before.slice(pos + 1);
  if (!atBoundary || /\s/.test(filter)) return null;
  return { pos, filter };
}

/** Remove sent text after acceptance; recalled edits appended during sends remain afterward. */
function withoutSubmittedText(current: string, submitted: string): string {
  const sent = submitted.trimEnd();
  if (!current.startsWith(sent)) return current;
  const rest = current.slice(sent.length);
  return rest.trim() ? rest.trimStart() : "";
}

// ---------------------------------------------------------------------------
// AgentComposer: input at Agent-panel bottoms.
// Queued-message trays sit directly above inputs; individually removable image attachments sit atop inputs; typing /
// opens skill menus above inputs. Sending remains available during replies; Send/Stop share buttons.
// ---------------------------------------------------------------------------

export function AgentComposer({
  ref,
  disabled,
  running,
  sending,
  placeholder,
  hidden,
  onSend,
  onInterrupt,
  onWithdrawQueued,
  onResendQueued,
  onSendQueuedNow,
}: AgentComposerProps) {
  const { t } = useTranslation("dashboard");
  const groupRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);
  const [text, setText] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);

  const [slash, setSlash] = useState<{ pos: number; filter: string } | null>(null);
  const [slashActive, setSlashActive] = useState<string | undefined>();
  const [slashListId, setSlashListId] = useState<string | undefined>();
  const [slashActiveId, setSlashActiveId] = useState<string | undefined>();
  const slashSkills = useSlashCommands(slash?.filter ?? null);
  const slashOpen = slashSkills.length > 0;
  const handleSlashIds = useCallback((listId: string | undefined, activeId: string | undefined) => {
    setSlashListId(listId);
    setSlashActiveId(activeId);
  }, []);

  const {
    images,
    error: attachError,
    isReading,
    addFiles,
    appendImages,
    removeImage,
    removeImages,
    invalidatePendingTranscodes,
  } = useImageAttachments();
  const hasQueuedMessages = useAssistantStore((s) => s.queuedMessages.length > 0);
  const attachDisabled = disabled || isReading || images.length >= MAX_ATTACHED_IMAGES;
  const hasContent = text.trim().length > 0 || images.length > 0;
  // Recalled queued messages may exceed image limits: retain all, requiring removal of excess before sending.
  const tooManyImages = images.length > MAX_ATTACHED_IMAGES;
  const canSend = !disabled && !isReading && hasContent && !tooManyImages;
  const previewImage = previewIndex === null ? undefined : images[previewIndex];

  const send = useCallback(() => {
    if (!canSend) return;
    invalidatePendingTranscodes();
    setSlash(null);
    const submittedText = text;
    const submittedImages = images;
    voidCall(
      onSend(submittedText.trim(), submittedImages.length > 0 ? submittedImages : undefined).then((accepted) => {
        if (!accepted) return;
        // Clear only text/images in this send; edits recalled during sends remain for users to send again.
        setText((current) => withoutSubmittedText(current, submittedText));
        removeImages(submittedImages.map((image) => image.id));
      }),
    );
  }, [canSend, images, invalidatePendingTranscodes, onSend, removeImages, text]);

  useImperativeHandle(ref, () => ({ send }), [send]);

  // External one-shot prefills (e.g. episode-empty-state entries through store.input) replace inputs, then clear store fields to avoid repetition.
  useEffect(() => {
    return useAssistantStore.subscribe((state, prev) => {
      if (!state.input || state.input === prev.input) return;
      setText(state.input);
      setSlash(null);
      // Defer clearing to microtasks, avoiding nested dispatch during zustand subscription notifications.
      void Promise.resolve().then(() => {
        useAssistantStore.getState().setInput("");
      });
      // Panels may have opened this frame with inert still applied; wait one frame before focusing.
      requestAnimationFrame(() => textareaRef.current?.focus());
    });
  }, []);

  // Recalled queue edits append text on new lines after existing text/images after current attachments, overwriting neither.
  useEffect(() => {
    const appendAll = (appends: ContentBlock[][]) => {
      for (const content of appends) {
        const appended = turnPlainText({ type: "user", content });
        if (appended) setText((current) => (current.trim() ? `${current.trimEnd()}\n${appended}` : appended));
        appendImages(turnImageAttachments({ type: "user", content }).map(imagePayloadToAttachment));
      }
    };
    appendAll(useAssistantStore.getState().takeComposerAppends());
    return useAssistantStore.subscribe((state, prev) => {
      if (state.composerAppends.length === 0 || state.composerAppends === prev.composerAppends) return;
      // Defer consumption to microtasks, avoiding nested dispatch during zustand subscription notifications.
      void Promise.resolve().then(() => {
        appendAll(useAssistantStore.getState().takeComposerAppends());
        requestAnimationFrame(() => textareaRef.current?.focus());
      });
    });
  }, [appendImages]);

  const selectSlashCommand = useCallback(
    (command: string) => {
      if (slash) {
        const rest = text.slice(slash.pos);
        const tokenEnd = rest.search(/\s/);
        const after = tokenEnd >= 0 ? text.slice(slash.pos + tokenEnd).trimStart() : "";
        setText(`${text.slice(0, slash.pos)}${command} ${after}`);
      }
      setSlash(null);
      textareaRef.current?.focus();
    },
    [slash, text],
  );

  const moveSlashActive = (step: number) => {
    // Menus highlight first entries before movement.
    const from = Math.max(0, slashSkills.findIndex((skill) => skill.name === slashActive));
    setSlashActive(slashSkills[(from + step + slashSkills.length) % slashSkills.length].name);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const composing = event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || composingRef.current;
    if (slashOpen && !composing) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        moveSlashActive(event.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        const skill = slashSkills.find((item) => item.name === slashActive) ?? slashSkills[0];
        selectSlashCommand(`/${skill.name}`);
        return;
      }
      if (event.key === "Escape") {
        // Close menus only; prevent defaults so this Esc cannot collapse shell panels.
        event.preventDefault();
        setSlash(null);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey && !composing) {
      event.preventDefault();
      send();
    }
  };

  const handleChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setText(value);
    const token = findSlashToken(value, event.target.selectionStart ?? value.length);
    setSlash(token);
    if (!token) setSlashActive(undefined);
  };

  const handlePaste = (event: React.ClipboardEvent) => {
    if (attachDisabled) return;
    const files = Array.from(event.clipboardData.items)
      .filter((item) => item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (files.length === 0) return;
    event.preventDefault();
    addFiles(files);
  };

  const handleDragOver = (event: React.DragEvent) => {
    if (attachDisabled || !Array.from(event.dataTransfer.items).some((item) => item.kind === "file")) return;
    event.preventDefault();
    setIsDragOver(true);
  };

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setIsDragOver(false);
    if (attachDisabled) return;
    const files = Array.from(event.dataTransfer.files).filter((file) => file.type.startsWith("image/"));
    if (files.length > 0) addFiles(files);
  };

  const hasAttachments = images.length > 0;

  return (
    <div hidden={hidden && !hasQueuedMessages} className="flex shrink-0 flex-col gap-2 border-t border-border p-3">
      <QueuedMessageTray
        onWithdraw={onWithdrawQueued}
        onResend={onResendQueued}
        onSendNow={onSendQueuedNow}
        questionPending={Boolean(hidden)}
        editDisabled={sending}
      />
      {/* Questionnaires replace inputs only; external trays still permit queue editing/deletion during questions. */}
      <div hidden={hidden}>
        {(attachError || tooManyImages) && (
          <p role="alert" className="mb-2 text-xs text-destructive">
            {tooManyImages ? t("composer_too_many_images_hint", { count: MAX_ATTACHED_IMAGES }) : attachError}
          </p>
        )}
        {/* Dragged images highlight entire inputs; highlight outer wrappers without changing input primitives. */}
        <div
          className={cn("rounded-lg transition-shadow duration-fast", isDragOver && "ring-2 ring-primary")}
          onDragOver={handleDragOver}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
        >
          <InputGroup ref={groupRef}>
            {hasAttachments && (
              <InputGroupAddon align="block-start">
                <AttachmentGroup className="w-full">
                  {images.map((image, index) => (
                    <Attachment key={image.id} size="xs">
                      <AttachmentMedia variant="image">
                        <img src={image.dataUrl} alt="" />
                      </AttachmentMedia>
                      <AttachmentContent>
                        <AttachmentTitle>{t("chat_image_attachment", { index: index + 1 })}</AttachmentTitle>
                      </AttachmentContent>
                      <AttachmentTrigger
                        aria-label={t("chat_image_enlarge", { index: index + 1 })}
                        onClick={() => setPreviewIndex(index)}
                      />
                      <AttachmentActions>
                        <AttachmentAction
                          aria-label={t("composer_remove_image", { index: index + 1 })}
                          onClick={() => removeImage(image.id)}
                        >
                          <X aria-hidden />
                        </AttachmentAction>
                      </AttachmentActions>
                    </Attachment>
                  ))}
                </AttachmentGroup>
              </InputGroupAddon>
            )}
            {/* Expand with content, capped at 40% of Agent-panel height, scrolling internally above caps. */}
            <InputGroupTextarea
              ref={textareaRef}
              role="combobox"
              rows={1}
              value={text}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              onPaste={handlePaste}
              onBlur={() => setSlash(null)}
              placeholder={placeholder}
              aria-label={t("assistant_input")}
              aria-autocomplete="list"
              aria-expanded={slashOpen}
              aria-controls={slashOpen ? slashListId : undefined}
              aria-activedescendant={slashOpen ? slashActiveId : undefined}
              disabled={disabled}
              className="min-h-9"
            />
            <InputGroupAddon align="block-end">
              <InputGroupButton
                size="icon-sm"
                onClick={() => fileInputRef.current?.click()}
                disabled={attachDisabled}
                aria-label={
                  images.length >= MAX_ATTACHED_IMAGES
                    ? t("max_images_hint", { count: MAX_ATTACHED_IMAGES })
                    : t("attach_image")
                }
              >
                <Paperclip aria-hidden />
              </InputGroupButton>
              {sending ? (
                <Button size="icon-sm" className="ml-auto" disabled aria-label={t("send_message_pending")}>
                  <Loader2 aria-hidden className="animate-spin" />
                </Button>
              ) : running && !hasContent ? (
                <Button variant="outline" size="icon-sm" className="ml-auto" onClick={onInterrupt} aria-label={t("stop_session")}>
                  <Square aria-hidden />
                </Button>
              ) : (
                <Button size="icon-sm" className="ml-auto" onClick={send} disabled={!canSend} aria-label={t("send_message")}>
                  <ArrowUp aria-hidden />
                </Button>
              )}
            </InputGroupAddon>
          </InputGroup>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*"
          aria-label={t("upload_attachment_aria")}
          className="hidden"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length > 0) addFiles(files);
            event.target.value = "";
          }}
        />

        <SlashCommandMenu
          anchor={groupRef}
          skills={slashSkills}
          active={slashActive}
          onActiveChange={setSlashActive}
          onSelect={selectSlashCommand}
          onClose={() => setSlash(null)}
          onIdsChange={handleSlashIds}
        />
      </div>

      <Dialog open={previewImage !== undefined} onOpenChange={(open) => !open && setPreviewIndex(null)}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>{t("chat_image_attachment", { index: (previewIndex ?? 0) + 1 })}</DialogTitle>
          </DialogHeader>
          {/* Only this area scrolls for very tall originals; it has no focusable children, so keyboard scrolling requires focusing the area itself */}
          <DialogBody tabIndex={0} role="region" aria-label={t("chat_image_attachment", { index: (previewIndex ?? 0) + 1 })}>
            {previewImage && <img src={previewImage.dataUrl} alt="" className="mx-auto block h-auto max-w-full rounded-md" />}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  );
}
