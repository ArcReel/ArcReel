import { useState, useRef, useCallback, useEffect, useId } from "react";
import { voidCall, voidPromise } from "@/utils/async";
import { Bot, Send, Square, Plus, ChevronDown, Trash2, MessageSquare, PanelRightClose, Paperclip, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import type { AttachedImage } from "@/hooks/useAssistantSession";
import { GlassPopover } from "@/components/ui/GlassPopover";
import { ContextBanner } from "./ContextBanner";
import { PendingQuestionWizard } from "./PendingQuestionWizard";
import { SlashCommandMenu } from "./SlashCommandMenu";
import type { SlashCommandMenuHandle } from "./SlashCommandMenu";
import { TodoListPanel } from "./TodoListPanel";
import { ChatMessage } from "./chat/ChatMessage";
import { composeAllTurns } from "./chat/utils";
import { uid } from "@/utils/id";

const MAX_IMAGES = 5;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024; // 5MB

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_TEXTAREA_HEIGHT_VH = 50;

// ---------------------------------------------------------------------------
// SessionSelector — 会话下拉选择器
// ---------------------------------------------------------------------------

function SessionSelector({
  onSwitch,
  onDelete,
}: {
  onSwitch: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const { sessions, currentSessionId, isDraftSession } = useAssistantStore();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();

  const currentSession = sessions.find((s) => s.id === currentSessionId);
  const displayTitle = isDraftSession ? t("new_session") : (currentSession?.title || formatTime(currentSession?.created_at, t));

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11.5px] transition-colors focus-ring"
        style={{ color: "var(--color-text-3)" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.6)";
          e.currentTarget.style.color = "var(--color-text)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--color-text-3)";
        }}
        title={t("switch_session")}
      >
        <MessageSquare className="h-3 w-3" />
        <span className="max-w-24 truncate">{displayTitle || t("no_session")}</span>
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {sessions.length > 0 && (
        <GlassPopover
          open={open}
          onClose={() => setOpen(false)}
          anchorRef={dropdownRef}
          sideOffset={4}
          width="w-64"
          layer="assistantLocalPopover"
          showHairline={false}
        >
          <div id={listboxId} role="menu" className="max-h-60 overflow-y-auto py-1">
            {sessions.map((session) => {
              const isActive = session.id === currentSessionId;
              const title = session.title || formatTime(session.created_at, t);
              return (
                <div
                  key={session.id}
                  className="group flex items-center gap-2 px-3 py-2 text-[12.5px] transition-colors"
                  style={
                    isActive
                      ? {
                          background: "var(--color-accent-dim)",
                          color: "var(--color-accent-2)",
                        }
                      : { color: "var(--color-text-2)" }
                  }
                  onMouseEnter={(e) => {
                    if (!isActive)
                      e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.5)";
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) e.currentTarget.style.background = "transparent";
                  }}
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => { onSwitch(session.id); setOpen(false); }}
                    className="flex flex-1 items-center gap-2 truncate text-left"
                  >
                    <StatusDot status={session.status} />
                    <span className="truncate">{title}</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={(e) => { e.stopPropagation(); if (confirm(t("confirm_delete_session"))) onDelete(session.id); }}
                    className="focus-ring shrink-0 rounded p-0.5 opacity-0 transition-all group-hover:opacity-100 focus-visible:opacity-100"
                    style={{ color: "var(--color-text-4)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "var(--color-danger)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = "var(--color-text-4)";
                    }}
                    title={t("delete_session")}
                    aria-label={t("delete_session")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        </GlassPopover>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    idle: "var(--color-text-4)",
    running: "var(--color-warn)",
    completed: "var(--color-good)",
    error: "var(--color-danger)",
    interrupted: "var(--color-text-3)",
  };
  return (
    <span
      className="h-1.5 w-1.5 shrink-0 rounded-full"
      style={{ background: colorMap[status] ?? "var(--color-text-4)" }}
    />
  );
}

function formatTime(isoStr: string | undefined, t: TFunction): string {
  if (!isoStr) return t("new_session");
  try {
    const d = new Date(isoStr);
    return `${(d.getMonth() + 1).toString().padStart(2, "0")}/${d.getDate().toString().padStart(2, "0")} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  } catch {
    return t("new_session");
  }
}

// ---------------------------------------------------------------------------
// AgentCopilot — 主面板
// ---------------------------------------------------------------------------

export function AgentCopilot() {
  const { t } = useTranslation(["dashboard", "common"]);
  const {
    turns, draftTurn, messagesLoading,
    sending, sessionStatus, pendingQuestion, answeringQuestion, error,
  } = useAssistantStore();

  const { currentProjectName } = useProjectsStore();
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const { sendMessage, answerQuestion, interrupt, createNewSession, switchSession, deleteSession } =
    useAssistantSession(currentProjectName);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageGenRef = useRef(0);
  const slashMenuRef = useRef<SlashCommandMenuHandle>(null);
  const [localInput, setLocalInput] = useState("");
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [attachedImages, setAttachedImages] = useState<AttachedImage[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const allTurns = composeAllTurns(turns, draftTurn);
  const isRunning = sessionStatus === "running";
  const inputDisabled = Boolean(pendingQuestion) || answeringQuestion || isRunning || sending;
  const attachDisabled = inputDisabled || attachedImages.length >= MAX_IMAGES;
  const inputPlaceholder = pendingQuestion
    ? t("answer_above_hint")
    : isRunning
      ? t("generating_stop_hint")
      : t("input_placeholder");

  const addImages = useCallback((files: File[]) => {
    setAttachError(null);
    const gen = imageGenRef.current;
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      if (file.size > MAX_IMAGE_BYTES) {
        setAttachError(t("image_too_large_hint", { name: file.name }));
        continue;
      }
      const reader = new FileReader();
      reader.onload = (e) => {
        if (imageGenRef.current !== gen) return; // stale — message already sent
        const dataUrl = e.target?.result as string;
        setAttachedImages((prev) => {
          if (prev.length >= MAX_IMAGES) return prev;
          return [...prev, { id: uid(), dataUrl, mimeType: file.type }];
        });
      };
      reader.readAsDataURL(file);
    }
  }, [t]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items);
    const imageItems = items.filter((item) => item.type.startsWith("image/"));
    if (imageItems.length === 0) return;
    e.preventDefault();
    const files = imageItems.map((item) => item.getAsFile()).filter(Boolean) as File[];
    addImages(files);
  }, [addImages]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    const hasFiles = Array.from(e.dataTransfer.items).some((i) => i.kind === "file");
    if (!hasFiles) return;
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/"));
    if (files.length > 0) addImages(files);
  }, [addImages]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length > 0) addImages(files);
    e.target.value = "";
  }, [addImages]);

  const removeImage = useCallback((id: string) => {
    setAttachedImages((prev) => prev.filter((img) => img.id !== id));
    setAttachError(null);
  }, []);

  const handleSend = useCallback(() => {
    if (inputDisabled || (!localInput.trim() && attachedImages.length === 0)) return;
    imageGenRef.current += 1; // invalidate pending FileReader callbacks
    voidCall(sendMessage(localInput.trim(), attachedImages.length > 0 ? attachedImages : undefined));
    setLocalInput("");
    setAttachedImages([]);
    setAttachError(null);
    setShowSlashMenu(false);
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [inputDisabled, localInput, attachedImages, sendMessage]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Delegate to slash menu when open
    if (showSlashMenu && slashMenuRef.current) {
      const consumed = slashMenuRef.current.handleKeyDown(e.key);
      if (consumed) {
        e.preventDefault();
        if (e.key === "Escape") setShowSlashMenu(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      const nativeEvent = e.nativeEvent;
      if (nativeEvent.isComposing || nativeEvent.keyCode === 229 || isComposingRef.current) {
        return;
      }
      e.preventDefault();
      handleSend();
    }
  }, [handleSend, showSlashMenu]);

  // Track the slash "/" position so we know where the command token starts
  const slashPosRef = useRef(-1);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    const cursor = e.target.selectionStart ?? val.length;
    setLocalInput(val);

    // Check text left of cursor: trigger menu when "/" is at start or after whitespace/newline
    const textBeforeCursor = val.slice(0, cursor);
    const lastSlash = textBeforeCursor.lastIndexOf("/");
    if (lastSlash >= 0) {
      const charBefore = lastSlash > 0 ? textBeforeCursor[lastSlash - 1] : undefined;
      const atBoundary = charBefore === undefined || /\s/.test(charBefore);
      const afterSlash = textBeforeCursor.slice(lastSlash + 1);
      const noSpaceAfterSlash = !afterSlash.includes(" ");
      if (atBoundary && noSpaceAfterSlash) {
        setShowSlashMenu(true);
        slashPosRef.current = lastSlash;
      } else {
        setShowSlashMenu(false);
        slashPosRef.current = -1;
      }
    } else {
      setShowSlashMenu(false);
      slashPosRef.current = -1;
    }

    // Auto-resize: grow upward until 50vh, then scroll
    const el = e.target;
    el.style.height = "auto";
    const maxH = window.innerHeight * (MAX_TEXTAREA_HEIGHT_VH / 100);
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`;
    el.style.overflowY = el.scrollHeight > maxH ? "auto" : "hidden";
  }, []);

  // Derive slash filter from input (text after "/" up to cursor)
  // eslint-disable-next-line react-hooks/refs -- slashPosRef 同时被 render 和 handleSlashSelect 使用，转 state 会引入 stale-closure 问题；此处仅用于过滤展示，不影响 UI 一致性
  const slashFilter = showSlashMenu && slashPosRef.current >= 0
    // eslint-disable-next-line react-hooks/refs -- 同上
    ? localInput.slice(slashPosRef.current + 1).split(/\s/)[0]
    : "";

  const handleSlashSelect = useCallback((cmd: string) => {
    // Replace the "/filter" token with the selected command, keep surrounding text
    const pos = slashPosRef.current;
    if (pos >= 0) {
      const before = localInput.slice(0, pos);
      // Find end of the slash token (next whitespace or end of string)
      const afterSlash = localInput.slice(pos);
      const tokenEnd = afterSlash.search(/\s/);
      const after = tokenEnd >= 0 ? localInput.slice(pos + tokenEnd) : "";
      setLocalInput(before + cmd + " " + after.trimStart());
    } else {
      setLocalInput(localInput + cmd + " ");
    }
    setShowSlashMenu(false);
    slashPosRef.current = -1;
    textareaRef.current?.focus();
  }, [localInput]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [allTurns.length]);

  return (
    <div
      className="relative isolate flex h-full flex-col"
      style={{ background: "oklch(0.19 0.011 250 / 0.5)" }}
    >
      {/* Header */}
      <div
        className="flex h-12 items-center gap-2 px-3"
        style={{ borderBottom: "1px solid var(--color-hairline)" }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <button
            type="button"
            onClick={toggleAssistantPanel}
            className="shrink-0 rounded p-1 transition-colors focus-ring"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.6)";
              e.currentTarget.style.color = "var(--color-text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--color-text-3)";
            }}
            title={t("collapse_panel")}
            aria-label={t("collapse_panel")}
          >
            <PanelRightClose aria-hidden className="h-4 w-4" />
          </button>
          <div
            className="grid h-6 w-6 shrink-0 place-items-center rounded-md"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent), oklch(0.60 0.10 280))",
              color: "oklch(0.12 0 0)",
            }}
          >
            <Bot className="h-3.5 w-3.5" />
          </div>
          {isRunning ? (
            <span
              className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12px]"
              style={{ color: "var(--color-accent-2)" }}
              title={t("arcreel_agent")}
            >
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full"
                style={{ background: "var(--color-accent)" }}
              />
              {t("thinking")}
            </span>
          ) : (
            <span className="display-serif min-w-0 truncate text-[13px] font-semibold leading-[1.1]">
              {t("arcreel_agent")}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <SessionSelector onSwitch={voidPromise(switchSession)} onDelete={voidPromise(deleteSession)} />
          <button
            type="button"
            onClick={createNewSession}
            className="rounded p-1 transition-colors focus-ring"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.6)";
              e.currentTarget.style.color = "var(--color-text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--color-text-3)";
            }}
            title={t("new_session")}
            aria-label={t("new_session")}
          >
            <Plus aria-hidden className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Context banner */}
      <ContextBanner />

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 min-w-0 space-y-3 overflow-y-auto overflow-x-hidden px-3 py-3">
        {allTurns.length === 0 && !messagesLoading && (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div
              className="mb-3 grid h-12 w-12 place-items-center rounded-2xl"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-accent-dim), oklch(0.22 0.011 265 / 0.6))",
                border: "1px solid var(--color-accent-soft)",
                boxShadow: "0 0 24px -8px var(--color-accent-glow)",
              }}
            >
              <Bot
                className="h-5 w-5"
                style={{ color: "var(--color-accent-2)" }}
              />
            </div>
            <p
              className="display-serif text-[14px] font-semibold"
              style={{ color: "var(--color-text)" }}
            >
              {t("start_chat_hint")}
            </p>
            <p
              className="mt-1 text-[11.5px]"
              style={{ color: "var(--color-text-3)" }}
            >
              {t("quick_skill_hint")}
            </p>
          </div>
        )}
        {allTurns.map((turn, i) => (
          <ChatMessage key={turn.uuid || `turn-${i}`} message={turn} />
        ))}
      </div>

      {pendingQuestion && (
        <PendingQuestionWizard
          pendingQuestion={pendingQuestion}
          answeringQuestion={answeringQuestion}
          error={error}
          onSubmitAnswers={voidPromise(answerQuestion)}
        />
      )}

      <TodoListPanel turns={turns} draftTurn={draftTurn} />

      {!pendingQuestion && (error || attachError) && (
        <div
          role="alert"
          aria-live="assertive"
          className="px-3 py-2 text-[11.5px]"
          style={{
            borderTop: "1px solid oklch(0.70 0.18 25 / 0.3)",
            background: "oklch(0.70 0.18 25 / 0.12)",
            color: "oklch(0.85 0.10 25)",
          }}
        >
          {error || attachError}
        </div>
      )}

      {/* Input area */}
      <div
        className="p-3"
        style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
      >
        {/* Thumbnail strip */}
        {attachedImages.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachedImages.map((img) => (
              <div key={img.id} className="relative">
                <button
                  type="button"
                  className="h-16 w-16 cursor-pointer border-0 bg-transparent p-0"
                  onClick={() => setLightboxSrc(img.dataUrl)}
                  aria-label={t("enlarge_image")}
                >
                  <img
                    src={img.dataUrl}
                    alt={t("assistant_input")}
                    className="h-16 w-16 rounded-md object-cover"
                    style={{ border: "1px solid var(--color-hairline)" }}
                  />
                </button>
                <button
                  type="button"
                  onClick={() => removeImage(img.id)}
                  className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full transition-colors focus-ring"
                  style={{
                    background: "oklch(0.14 0.008 265)",
                    color: "var(--color-text-2)",
                    border: "1px solid var(--color-hairline)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--color-danger)";
                    e.currentTarget.style.color = "oklch(0.14 0 0)";
                    e.currentTarget.style.borderColor = "var(--color-danger)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "oklch(0.14 0.008 265)";
                    e.currentTarget.style.color = "var(--color-text-2)";
                    e.currentTarget.style.borderColor = "var(--color-hairline)";
                  }}
                  aria-label={t("remove_image")}
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className="relative flex items-end gap-2 rounded-lg px-3 py-2 transition-colors"
          style={{
            border: `1px solid ${isDragOver ? "var(--color-accent)" : "var(--color-hairline)"}`,
            background: isDragOver
              ? "var(--color-accent-dim)"
              : "oklch(0.20 0.012 265 / 0.7)",
            backdropFilter: "blur(8px)",
            WebkitBackdropFilter: "blur(8px)",
            boxShadow: isDragOver
              ? "0 0 0 3px var(--color-accent-soft), inset 0 1px 0 oklch(1 0 0 / 0.04)"
              : "inset 0 1px 0 oklch(1 0 0 / 0.04)",
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {showSlashMenu && (
            <SlashCommandMenu
              ref={slashMenuRef}
              filter={slashFilter}
              onSelect={handleSlashSelect}
            />
          )}
          <textarea
            ref={textareaRef}
            role="combobox"
            value={localInput}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => {
              isComposingRef.current = true;
            }}
            onCompositionEnd={() => {
              isComposingRef.current = false;
            }}
            onPaste={handlePaste}
            placeholder={inputPlaceholder}
            rows={1}
            aria-label={t("assistant_input")}
            aria-expanded={showSlashMenu}
            aria-controls={showSlashMenu ? "slash-command-menu" : undefined}
            aria-activedescendant={
              // eslint-disable-next-line react-hooks/refs -- aria-activedescendant 需实时读取 slashMenuRef 的派生值，改用回调 prop 需修改 SlashCommandMenu 接口，超出范围
              slashMenuRef.current?.activeDescendantId
            }
            className="flex-1 resize-none overflow-hidden bg-transparent text-[13px] outline-none"
            style={{
              maxHeight: `${MAX_TEXTAREA_HEIGHT_VH}vh`,
              color: "var(--color-text)",
            }}
            disabled={inputDisabled}
          />

          {/* Attachment button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachDisabled}
            className="shrink-0 rounded p-1.5 transition-colors focus-ring disabled:opacity-30"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              if (!attachDisabled) {
                e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.6)";
                e.currentTarget.style.color = "var(--color-text)";
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--color-text-3)";
            }}
            title={attachedImages.length >= MAX_IMAGES ? t("max_images_hint", { count: MAX_IMAGES }) : t("attach_image")}
            aria-label={t("attach_image")}
          >
            <Paperclip className="h-4 w-4" />
          </button>

          {isRunning ? (
            <button
              onClick={voidPromise(interrupt)}
              className="shrink-0 rounded p-1.5 transition-colors focus-ring"
              style={{ color: "var(--color-danger)" }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "oklch(0.70 0.18 25 / 0.15)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
              }}
              title={t("stop_session")}
              aria-label={t("stop_session")}
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={(!localInput.trim() && attachedImages.length === 0) || inputDisabled}
              className="shrink-0 rounded-md p-1.5 transition-opacity focus-ring disabled:cursor-not-allowed disabled:opacity-30"
              style={{
                color: "oklch(0.14 0 0)",
                background:
                  "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 4px 14px -4px var(--color-accent-glow)",
              }}
              title={t("send_message")}
              aria-label={t("send_message")}
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*"
          aria-label={t("upload_attachment_aria")}
          className="hidden"
          onChange={handleFileSelect}
        />
      </div>

      {lightboxSrc && (
        <ImageLightbox
          src={lightboxSrc}
          alt={t("assistant_input")}
          onClose={() => setLightboxSrc(null)}
        />
      )}
    </div>
  );
}
