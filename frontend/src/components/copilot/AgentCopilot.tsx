import { useState, useRef, useCallback, useEffect, useId } from "react";
import { voidCall, voidPromise } from "@/utils/async";
import { Bot, Send, Square, Plus, ChevronDown, Trash2, MessageSquare, PanelRightClose, Paperclip, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ImageLightbox } from "@/components/shared/ImageLightbox";
import { Textarea } from "@/components/ui/textarea";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import type { ImagePayload } from "@/types";
import { MAX_ATTACHED_IMAGES, useImageAttachments } from "@/hooks/useImageAttachments";
import { GlassPopover } from "@/components/legacy/GlassPopover";
import { ContextBanner } from "./ContextBanner";
import { PendingQuestionWizard } from "./PendingQuestionWizard";
import { SlashCommandMenu } from "./SlashCommandMenu";
import type { SlashCommandMenuHandle } from "./SlashCommandMenu";
import { TodoListPanel } from "./TodoListPanel";
import { MessageFlow, type MessageFlowHandle } from "./chat/MessageFlow";
import { formatShortDateTime } from "@/utils/date-format";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------


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
        className="flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11.5px] transition-colors focus-ring"
        style={{ color: "var(--muted-foreground)" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.6)";
          e.currentTarget.style.color = "var(--foreground)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--muted-foreground)";
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
                          background: "color-mix(in oklab, var(--primary) 12%, transparent)",
                          color: "var(--primary)",
                        }
                      : { color: "var(--subtle-foreground)" }
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
                    className="focus-ring shrink-0 rounded-sm p-0.5 opacity-0 transition-all group-hover:opacity-100 focus-visible:opacity-100"
                    style={{ color: "var(--muted-foreground)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "var(--destructive)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = "var(--muted-foreground)";
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
    idle: "var(--muted-foreground)",
    running: "var(--warn)",
    completed: "var(--good)",
    error: "var(--destructive)",
    interrupted: "var(--muted-foreground)",
  };
  return (
    <span
      className="h-1.5 w-1.5 shrink-0 rounded-full"
      style={{ background: colorMap[status] ?? "var(--muted-foreground)" }}
    />
  );
}

function formatTime(isoStr: string | undefined, t: TFunction): string {
  return formatShortDateTime(isoStr) ?? t("new_session");
}

// ---------------------------------------------------------------------------
// AgentCopilot — 主面板
// ---------------------------------------------------------------------------

export function AgentCopilot() {
  const { t } = useTranslation(["dashboard", "common"]);
  const {
    turns, draftTurn, currentSessionId, sending, sessionStatus, pendingQuestion, answeringQuestion, error,
    startupFailureOrigin,
  } = useAssistantStore();

  const { currentProjectName } = useProjectsStore();
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const { sendMessage, rewriteMessage, answerQuestion, interrupt, createNewSession, switchSession, deleteSession } =
    useAssistantSession(currentProjectName);

  const flowRef = useRef<MessageFlowHandle>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const slashMenuRef = useRef<SlashCommandMenuHandle>(null);
  const [localInput, setLocalInput] = useState("");
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const {
    images: attachedImages,
    error: attachError,
    isReading: isReadingImages,
    addFiles: addImages,
    removeImage,
    resetImages,
    invalidatePendingTranscodes,
  } = useImageAttachments();
  const [isDragOver, setIsDragOver] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const isRunning = sessionStatus === "running";
  const inputDisabled = Boolean(pendingQuestion) || answeringQuestion || isRunning || sending;
  const attachDisabled = inputDisabled || isReadingImages || attachedImages.length >= MAX_ATTACHED_IMAGES;
  const inputPlaceholder = pendingQuestion
    ? t("answer_above_hint")
    : isRunning
      ? t("generating_stop_hint")
      : t("input_placeholder");

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    if (attachDisabled) return;
    const items = Array.from(e.clipboardData.items);
    const imageItems = items.filter((item) => item.type.startsWith("image/"));
    if (imageItems.length === 0) return;
    e.preventDefault();
    const files = imageItems.map((item) => item.getAsFile()).filter(Boolean) as File[];
    addImages(files);
  }, [addImages, attachDisabled]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (attachDisabled) return;
    const hasFiles = Array.from(e.dataTransfer.items).some((i) => i.kind === "file");
    if (!hasFiles) return;
    e.preventDefault();
    setIsDragOver(true);
  }, [attachDisabled]);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (attachDisabled) return;
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/"));
    if (files.length > 0) addImages(files);
  }, [addImages, attachDisabled]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length > 0) addImages(files);
    e.target.value = "";
  }, [addImages]);

  const handleSend = useCallback(() => {
    if (inputDisabled || isReadingImages || (!localInput.trim() && attachedImages.length === 0)) return;
    invalidatePendingTranscodes();
    setShowSlashMenu(false);
    // 发送即回到最新处，随后到达的这条消息与回复都在视野里
    flowRef.current?.scrollToEnd();
    // 发送期间输入锁定（sending 置位）；受理成功才清空，失败保留内容供重试
    voidCall(
      sendMessage(localInput.trim(), attachedImages.length > 0 ? attachedImages : undefined).then(
        (accepted) => {
          if (!accepted) return;
          setLocalInput("");
          resetImages();
        },
      ),
    );
  }, [
    inputDisabled,
    isReadingImages,
    localInput,
    attachedImages,
    sendMessage,
    invalidatePendingTranscodes,
    resetImages,
  ]);

  // 改写成功后由会话切换重建时间线（编辑态随 resetTimeline 清空）；失败保留编辑态，
  // 用户可以改完再试，错误经消息区上方的错误条呈现
  const handleSubmitEdit = useCallback((turnUuid: string, text: string, images: ImagePayload[]) => {
    voidCall(rewriteMessage(turnUuid, text, images));
  }, [rewriteMessage]);

  // 提交回答与发送消息同理：回到最新处等 Agent 接着回复
  const handleSubmitAnswers = useCallback((questionId: string, answers: Record<string, string>) => {
    flowRef.current?.scrollToEnd();
    voidCall(answerQuestion(questionId, answers));
  }, [answerQuestion]);

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

  // 消费外部投递的一次性预填文本（如分集空态 CTA 经 store.input 投递）：
  // 写入本地输入框后清空 store 字段，避免残留触发重复预填。
  // 覆盖而非追加——预填来自用户的明确点击意图。
  useEffect(() => {
    return useAssistantStore.subscribe((state, prev) => {
      if (!state.input || state.input === prev.input) return;
      setLocalInput(state.input);
      // 延后到微任务清空，避免在 zustand 订阅通知期间嵌套 dispatch
      void Promise.resolve().then(() => {
        useAssistantStore.getState().setInput("");
      });
      // 面板可能同帧刚被打开（inert 尚未移除），等一帧再聚焦
      requestAnimationFrame(() => textareaRef.current?.focus());
    });
  }, []);

  return (
    <div
      className="relative isolate flex h-full flex-col"
      style={{ background: "oklch(0.19 0.011 250 / 0.5)" }}
    >
      {/* Header */}
      <div
        className="flex h-12 items-center gap-2 px-3"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <button
            type="button"
            onClick={toggleAssistantPanel}
            className="shrink-0 rounded-sm p-1 transition-colors focus-ring"
            style={{ color: "var(--muted-foreground)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.6)";
              e.currentTarget.style.color = "var(--foreground)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--muted-foreground)";
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
                "linear-gradient(135deg, var(--primary), oklch(0.60 0.10 280))",
              color: "oklch(0.12 0 0)",
            }}
          >
            <Bot className="h-3.5 w-3.5" />
          </div>
          {isRunning || sending ? (
            <span
              className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12px]"
              style={{ color: "var(--primary)" }}
              title={t("arcreel_agent")}
            >
              <span
                className="h-1.5 w-1.5 animate-breath rounded-full"
                style={{ background: "var(--primary)" }}
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
            className="rounded-sm p-1 transition-colors focus-ring"
            style={{ color: "var(--muted-foreground)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.6)";
              e.currentTarget.style.color = "var(--foreground)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--muted-foreground)";
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
      <MessageFlow
        key={currentSessionId ?? "draft"}
        ref={flowRef}
        onSubmitEdit={handleSubmitEdit}
        // 改写失败时原始输入留在仍开着的编辑器里，重试由它的「重新发送」发起：
        // 卡片这里给重试只会重放主输入框的无关内容（为空时更是毫无反应）
        onRetryStartup={startupFailureOrigin === "rewrite" ? undefined : handleSend}
      />

      {pendingQuestion && (
        <PendingQuestionWizard
          pendingQuestion={pendingQuestion}
          answeringQuestion={answeringQuestion}
          error={error}
          onSubmitAnswers={handleSubmitAnswers}
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
        style={{ borderTop: "1px solid color-mix(in oklab, var(--border) 50%, transparent)" }}
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
                    style={{ border: "1px solid var(--border)" }}
                  />
                </button>
                <button
                  type="button"
                  onClick={() => removeImage(img.id)}
                  className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full transition-colors focus-ring"
                  style={{
                    background: "oklch(0.14 0.008 265)",
                    color: "var(--subtle-foreground)",
                    border: "1px solid var(--border)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--destructive)";
                    e.currentTarget.style.color = "oklch(0.14 0 0)";
                    e.currentTarget.style.borderColor = "var(--destructive)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "oklch(0.14 0.008 265)";
                    e.currentTarget.style.color = "var(--subtle-foreground)";
                    e.currentTarget.style.borderColor = "var(--border)";
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
            border: `1px solid ${isDragOver ? "var(--primary)" : "var(--border)"}`,
            background: isDragOver
              ? "color-mix(in oklab, var(--primary) 12%, transparent)"
              : "oklch(0.20 0.012 265 / 0.7)",
            backdropFilter: "blur(8px)",
            WebkitBackdropFilter: "blur(8px)",
            boxShadow: isDragOver
              ? "0 0 0 3px color-mix(in oklab, var(--primary) 22%, transparent), inset 0 1px 0 oklch(1 0 0 / 0.04)"
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
          {/* 随内容撑高，上限是 Agent 面板高度的 40%（外壳把面板设为尺寸容器），超出后在框内滚动；
              程序预填与面板调宽都会重新计算高度 */}
          <Textarea
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
            className="min-h-0 flex-1 rounded-none border-0 bg-transparent px-0 py-0 text-[13px] text-foreground focus-visible:ring-0 disabled:bg-transparent dark:bg-transparent dark:disabled:bg-transparent"
            disabled={inputDisabled}
          />

          {/* Attachment button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachDisabled}
            className="shrink-0 rounded-sm p-1.5 transition-colors focus-ring disabled:opacity-30"
            style={{ color: "var(--muted-foreground)" }}
            onMouseEnter={(e) => {
              if (!attachDisabled) {
                e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.6)";
                e.currentTarget.style.color = "var(--foreground)";
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--muted-foreground)";
            }}
            title={attachedImages.length >= MAX_ATTACHED_IMAGES ? t("max_images_hint", { count: MAX_ATTACHED_IMAGES }) : t("attach_image")}
            aria-label={t("attach_image")}
          >
            <Paperclip className="h-4 w-4" />
          </button>

          {isRunning ? (
            <button
              onClick={voidPromise(interrupt)}
              className="shrink-0 rounded-sm p-1.5 transition-colors focus-ring"
              style={{ color: "var(--destructive)" }}
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
              disabled={
                (!localInput.trim() && attachedImages.length === 0) || inputDisabled || isReadingImages
              }
              className="shrink-0 rounded-md p-1.5 transition-opacity focus-ring disabled:cursor-not-allowed disabled:opacity-30"
              style={{
                color: "oklch(0.14 0 0)",
                background:
                  "var(--primary)",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 4px 14px -4px color-mix(in oklab, var(--primary) 35%, transparent)",
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
