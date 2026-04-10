import { useState, useRef, useCallback, useEffect } from "react";
import { Bot, Send, Square, Plus, ChevronDown, Trash2, MessageSquare, PanelRightClose, Paperclip, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import type { AttachedImage } from "@/hooks/useAssistantSession";
import { Popover } from "@/components/ui/Popover";
import { ContextBanner } from "./ContextBanner";
import { PendingQuestionWizard } from "./PendingQuestionWizard";
import { SlashCommandMenu } from "./SlashCommandMenu";
import type { SlashCommandMenuHandle } from "./SlashCommandMenu";
import { TodoListPanel } from "./TodoListPanel";
import { ChatMessage } from "./chat/ChatMessage";
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

  const currentSession = sessions.find((s) => s.id === currentSessionId);
  const displayTitle = isDraftSession ? t("new_session") : (currentSession?.title || formatTime(currentSession?.created_at, t));

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
        title={t("switch_session")}
      >
        <MessageSquare className="h-3 w-3" />
        <span className="max-w-24 truncate">{displayTitle || t("no_session")}</span>
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {sessions.length > 0 && (
        <Popover
          open={open}
          onClose={() => setOpen(false)}
          anchorRef={dropdownRef}
          sideOffset={4}
          width="w-64"
          layer="assistantLocalPopover"
          className="rounded-lg border border-gray-700 shadow-xl"
        >
          <div className="max-h-60 overflow-y-auto py-1">
            {sessions.map((session) => {
              const isActive = session.id === currentSessionId;
              const title = session.title || formatTime(session.created_at, t);
              return (
                <div
                  key={session.id}
                  className={`group flex items-center gap-2 px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? "bg-indigo-500/10 text-indigo-300"
                      : "text-gray-300 hover:bg-gray-800"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => { onSwitch(session.id); setOpen(false); }}
                    className="flex flex-1 items-center gap-2 truncate text-left"
                  >
                    <StatusDot status={session.status} />
                    <span className="truncate">{title}</span>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); if (confirm(t("confirm_delete_session"))) onDelete(session.id); }}
                    className="shrink-0 rounded p-0.5 text-gray-600 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                    title={t("delete_session")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        </Popover>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    idle: "bg-gray-500",
    running: "bg-amber-400",
    completed: "bg-green-500",
    error: "bg-red-500",
    interrupted: "bg-gray-400",
  };
  return (
    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${colorMap[status] ?? "bg-gray-500"}`} />
  );
}

function formatTime(isoStr: string | undefined, t: any): string {
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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageGenRef = useRef(0);
  const slashMenuRef = useRef<SlashCommandMenuHandle>(null);
  const [localInput, setLocalInput] = useState("");
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [attachedImages, setAttachedImages] = useState<AttachedImage[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const allTurns = draftTurn ? [...turns, draftTurn] : turns;
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
    sendMessage(localInput.trim(), attachedImages.length > 0 ? attachedImages : undefined);
    setLocalInput("");
    setAttachedImages([]);
    setAttachError(null);
    setShowSlashMenu(false);
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [inputDisabled, localInput, attachedImages, sendMessage]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
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
  const slashFilter = showSlashMenu && slashPosRef.current >= 0
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
    <div className="relative isolate flex h-full flex-col">
      {/* Header */}
      <div className="flex h-10 items-center justify-between border-b border-gray-800 px-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleAssistantPanel}
            className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
            title={t("collapse_panel")}
          >
            <PanelRightClose className="h-4 w-4" />
          </button>
          <Bot className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-medium text-gray-300">{t("arcreel_agent")}</span>
        </div>
        <div className="flex items-center gap-1">
          {isRunning && (
            <span className="flex items-center gap-1.5 text-xs text-indigo-400 mr-1">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400" />
              {t("thinking")}
            </span>
          )}
          <SessionSelector onSwitch={switchSession} onDelete={deleteSession} />
          <button
            type="button"
            onClick={createNewSession}
            className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
            title={t("new_session")}
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Context banner */}
      <ContextBanner />

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-3 py-3 space-y-3">
        {allTurns.length === 0 && !messagesLoading && (
          <div className="flex h-full flex-col items-center justify-center text-center text-gray-500">
            <Bot className="mb-3 h-8 w-8 text-gray-600" />
            <p className="text-sm">{t("start_chat_hint")}</p>
            <p className="mt-1 text-xs text-gray-600">
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
          onSubmitAnswers={answerQuestion}
        />
      )}

      <TodoListPanel turns={turns} draftTurn={draftTurn} />

      {!pendingQuestion && (error || attachError) && (
        <div className="border-t border-red-400/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error || attachError}
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-gray-800 p-3">
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
                    className="h-16 w-16 rounded-md object-cover border border-gray-600"
                  />
                </button>
                <button
                  type="button"
                  onClick={() => removeImage(img.id)}
                  className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-gray-900 text-gray-300 hover:bg-red-500 hover:text-white"
                  aria-label={t("remove_image")}
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className={`relative flex items-end gap-2 rounded-lg border bg-gray-800 px-3 py-2 transition-colors ${
            isDragOver ? "border-indigo-500 bg-indigo-500/10" : "border-gray-700"
          }`}
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
            onPaste={handlePaste}
            placeholder={inputPlaceholder}
            rows={1}
            aria-label={t("assistant_input")}
            aria-expanded={showSlashMenu}
            aria-controls={showSlashMenu ? "slash-command-menu" : undefined}
            aria-activedescendant={slashMenuRef.current?.activeDescendantId}
            className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-500 outline-none overflow-hidden"
            style={{ maxHeight: `${MAX_TEXTAREA_HEIGHT_VH}vh` }}
            disabled={inputDisabled}
          />

          {/* Attachment button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachDisabled}
            className="shrink-0 rounded p-1.5 text-gray-400 hover:bg-gray-700 hover:text-gray-200 disabled:opacity-30"
            title={attachedImages.length >= MAX_IMAGES ? t("max_images_hint", { count: MAX_IMAGES }) : t("attach_image")}
            aria-label={t("attach_image")}
          >
            <Paperclip className="h-4 w-4" />
          </button>

          {isRunning ? (
            <button
              onClick={interrupt}
              className="shrink-0 rounded p-1.5 text-red-400 hover:bg-gray-700"
              title={t("stop_session")}
              aria-label={t("stop_session")}
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={(!localInput.trim() && attachedImages.length === 0) || inputDisabled}
              className="shrink-0 rounded p-1.5 text-indigo-400 hover:bg-gray-700 disabled:opacity-30"
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
