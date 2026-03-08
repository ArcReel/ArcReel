import { useState, useRef, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";
import { Bot, Send, Square, Plus, ChevronDown, Trash2, MessageSquare, PanelRightClose } from "lucide-react";
import { useAnchoredPopover } from "@/hooks/useAnchoredPopover";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { UI_LAYERS } from "@/utils/ui-layers";
import { ContextBanner } from "./ContextBanner";
import { PendingQuestionWizard } from "./PendingQuestionWizard";
import { SlashCommandMenu } from "./SlashCommandMenu";
import type { SlashCommandMenuHandle } from "./SlashCommandMenu";
import { TodoListPanel } from "./TodoListPanel";
import { ChatMessage } from "./chat/ChatMessage";

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
  const { sessions, currentSessionId, isDraftSession } = useAssistantStore();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const { panelRef, positionStyle } = useAnchoredPopover({
    open,
    anchorRef: dropdownRef,
    onClose: () => setOpen(false),
    sideOffset: 4,
  });

  const currentSession = sessions.find((s) => s.id === currentSessionId);
  const displayTitle = isDraftSession ? "新会话" : (currentSession?.title || formatTime(currentSession?.created_at));

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
        title="切换会话"
      >
        <MessageSquare className="h-3 w-3" />
        <span className="max-w-24 truncate">{displayTitle || "无会话"}</span>
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && sessions.length > 0 && typeof document !== "undefined" && createPortal(
        <div
          ref={panelRef}
          className={`fixed w-64 rounded-lg border border-gray-700 shadow-xl ${UI_LAYERS.assistantLocalPopover}`}
          style={{
            ...positionStyle,
            backgroundColor: "rgb(17 24 39)",
          }}
        >
          <div className="max-h-60 overflow-y-auto py-1">
            {sessions.map((session) => {
              const isActive = session.id === currentSessionId;
              const title = session.title || formatTime(session.created_at);
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
                    onClick={(e) => { e.stopPropagation(); onDelete(session.id); }}
                    className="shrink-0 rounded p-0.5 text-gray-600 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                    title="删除会话"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        </div>,
        document.body,
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

function formatTime(isoStr: string | undefined): string {
  if (!isoStr) return "新会话";
  try {
    const d = new Date(isoStr);
    return `${(d.getMonth() + 1).toString().padStart(2, "0")}/${d.getDate().toString().padStart(2, "0")} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  } catch {
    return "新会话";
  }
}

// ---------------------------------------------------------------------------
// AgentCopilot — 主面板
// ---------------------------------------------------------------------------

export function AgentCopilot() {
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
  const slashMenuRef = useRef<SlashCommandMenuHandle>(null);
  const [localInput, setLocalInput] = useState("");
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const allTurns = draftTurn ? [...turns, draftTurn] : turns;
  const isRunning = sessionStatus === "running";
  const inputDisabled = Boolean(pendingQuestion) || answeringQuestion || isRunning || sending;
  const inputPlaceholder = pendingQuestion
    ? "请先回答上方问题"
    : isRunning
      ? "助手正在生成中，可点击停止中断"
      : "输入消息，输入 / 查看可用技能";

  const handleSend = useCallback(() => {
    if (inputDisabled || !localInput.trim()) return;
    sendMessage(localInput.trim());
    setLocalInput("");
    setShowSlashMenu(false);
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [inputDisabled, localInput, sendMessage]);

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
    const maxH = window.innerHeight * 0.5;
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
            title="收起助手面板"
          >
            <PanelRightClose className="h-4 w-4" />
          </button>
          <Bot className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-medium text-gray-300">ArcReel 智能体</span>
        </div>
        <div className="flex items-center gap-1">
          {isRunning && (
            <span className="flex items-center gap-1.5 text-xs text-indigo-400 mr-1">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400" />
              思考中
            </span>
          )}
          <SessionSelector onSwitch={switchSession} onDelete={deleteSession} />
          <button
            type="button"
            onClick={createNewSession}
            className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
            title="新建会话"
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
            <p className="text-sm">在下方输入消息开始对话</p>
            <p className="mt-1 text-xs text-gray-600">
              输入 / 可快速调用技能
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

      {!pendingQuestion && error && (
        <div className="border-t border-red-400/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-gray-800 p-3">
        <div className="relative flex items-end gap-2 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2">
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
            placeholder={inputPlaceholder}
            rows={1}
            aria-label="助手输入"
            aria-expanded={showSlashMenu}
            aria-controls={showSlashMenu ? "slash-command-menu" : undefined}
            aria-activedescendant={slashMenuRef.current?.activeDescendantId}
            className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-500 outline-none overflow-hidden"
            style={{ maxHeight: "50vh" }}
            disabled={inputDisabled}
          />
          {isRunning ? (
            <button
              onClick={interrupt}
              className="shrink-0 rounded p-1.5 text-red-400 hover:bg-gray-700"
              title="中断会话"
              aria-label="中断会话"
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!localInput.trim() || inputDisabled}
              className="shrink-0 rounded p-1.5 text-indigo-400 hover:bg-gray-700 disabled:opacity-30"
              title="发送消息"
              aria-label="发送消息"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
