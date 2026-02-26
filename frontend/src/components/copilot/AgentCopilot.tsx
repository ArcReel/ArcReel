import { useState, useRef, useCallback } from "react";
import { Bot, Send, Square } from "lucide-react";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { ContextBanner } from "./ContextBanner";
import { SkillPills } from "./SkillPills";
import { ChatMessage } from "./chat/ChatMessage";

export function AgentCopilot() {
  const {
    turns, draftTurn, messagesLoading,
    sending, sessionStatus,
  } = useAssistantStore();

  const { currentProjectName } = useProjectsStore();
  const { sendMessage, interrupt } = useAssistantSession(currentProjectName);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [localInput, setLocalInput] = useState("");

  const handleSend = useCallback(() => {
    if (!localInput.trim()) return;
    sendMessage(localInput.trim());
    setLocalInput("");
  }, [localInput, sendMessage]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  const allTurns = draftTurn ? [...turns, draftTurn] : turns;
  const isRunning = sessionStatus === "running";

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex h-10 items-center justify-between border-b border-gray-800 px-4">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-medium text-gray-300">AI 副驾驶</span>
        </div>
        {isRunning && (
          <span className="flex items-center gap-1.5 text-xs text-indigo-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400" />
            思考中
          </span>
        )}
      </div>

      {/* Context banner */}
      <ContextBanner />

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {allTurns.length === 0 && !messagesLoading && (
          <div className="flex h-full flex-col items-center justify-center text-center text-gray-500">
            <Bot className="mb-3 h-8 w-8 text-gray-600" />
            <p className="text-sm">在下方输入消息开始对话</p>
            <p className="mt-1 text-xs text-gray-600">
              或使用技能快捷按钮执行常用操作
            </p>
          </div>
        )}
        {allTurns.map((turn, i) => (
          <ChatMessage key={turn.uuid || `turn-${i}`} message={turn} />
        ))}
      </div>

      {/* Skill pills */}
      <SkillPills onSendCommand={(cmd) => setLocalInput(cmd)} />

      {/* Input area */}
      <div className="border-t border-gray-800 p-3">
        <div className="flex items-end gap-2 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2">
          <textarea
            value={localInput}
            onChange={(e) => setLocalInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息..."
            rows={1}
            className="flex-1 resize-none bg-transparent text-sm text-gray-200 placeholder-gray-500 outline-none"
          />
          {isRunning ? (
            <button onClick={interrupt} className="rounded p-1.5 text-red-400 hover:bg-gray-700">
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!localInput.trim() || sending}
              className="rounded p-1.5 text-indigo-400 hover:bg-gray-700 disabled:opacity-30"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
