import { useCallback, useRef, useState } from "react";
import { History, PanelRightClose, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import type { AttachedImage } from "@/hooks/useImageAttachments";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ImagePayload } from "@/types";
import { voidCall } from "@/utils/async";
import { AgentComposer, type AgentComposerHandle } from "./AgentComposer";
import { FreeImageResult } from "./FreeImageResult";
import { FreeImageDialog } from "./FreeImageDialog";
import { RunSettings } from "@/components/shared/RunSettings";
import { AgentQuestionnaire } from "./AgentQuestionnaire";
import { SessionHistory } from "./SessionHistory";
import { sessionTitle } from "./session-title";
import { TodoProgress } from "./TodoProgress";
import { MessageFlow, type MessageFlowHandle } from "./chat/MessageFlow";


export function AgentCopilot() {
  const { t } = useTranslation(["dashboard", "common"]);
  const turns = useAssistantStore((s) => s.turns);
  const draftTurn = useAssistantStore((s) => s.draftTurn);
  const currentSessionId = useAssistantStore((s) => s.currentSessionId);
  const sessions = useAssistantStore((s) => s.sessions);
  const isDraftSession = useAssistantStore((s) => s.isDraftSession);
  const sending = useAssistantStore((s) => s.sending);
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  const pendingQuestion = useAssistantStore((s) => s.pendingQuestion);
  const answeringQuestion = useAssistantStore((s) => s.answeringQuestion);
  const error = useAssistantStore((s) => s.error);
  const startupFailureOrigin = useAssistantStore((s) => s.startupFailureOrigin);

  const { currentProjectName } = useProjectsStore();
  const toggleAssistantPanel = useAppStore((s) => s.toggleAssistantPanel);
  const {
    sendMessage,
    rewriteMessage,
    answerQuestion,
    interrupt,
    createNewSession,
    switchSession,
    deleteSession,
    withdrawQueuedMessage,
    resendQueuedMessage,
    sendQueuedMessageNow,
  } = useAssistantSession(currentProjectName);

  const flowRef = useRef<MessageFlowHandle>(null);
  const composerRef = useRef<AgentComposerHandle>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [freeImageRequest, setFreeImageRequest] = useState<{ project: string; prompt: string } | null>(null);
  const [freeImageTask, setFreeImageTask] = useState<{ project: string; task_id: string; draft_id: string } | null>(null);

  const isRunning = sessionStatus === "running";
  const isWorking = isRunning || sending;
  // 回复进行中输入框照常可用：此时发出的消息进排队消息托盘
  const inputDisabled = Boolean(pendingQuestion) || answeringQuestion || sending;
  const title =
    isDraftSession || !currentSessionId
      ? t("dashboard:new_session")
      : sessionTitle(
          sessions.find((s) => s.id === currentSessionId),
          t,
        );

  const handleSend = useCallback(
    (text: string, images?: AttachedImage[]) => {
      setHistoryOpen(false);
      flowRef.current?.scrollToEnd();
      return sendMessage(text, images);
    },
    [sendMessage],
  );

  const handleResendQueued = useCallback((id: string) => {
    setHistoryOpen(false);
    flowRef.current?.scrollToEnd();
    return resendQueuedMessage(id);
  }, [resendQueuedMessage]);

  const handleSendQueuedNow = useCallback((id: string) => {
    setHistoryOpen(false);
    flowRef.current?.scrollToEnd();
    return sendQueuedMessageNow(id);
  }, [sendQueuedMessageNow]);

  const handleSubmitEdit = useCallback(
    (turnUuid: string, text: string, images: ImagePayload[]) => {
      voidCall(rewriteMessage(turnUuid, text, images));
    },
    [rewriteMessage],
  );

  const handleSubmitAnswers = useCallback(
    (questionId: string, answers: Record<string, string>) => {
      setHistoryOpen(false);
      flowRef.current?.scrollToEnd();
      voidCall(answerQuestion(questionId, answers));
    },
    [answerQuestion],
  );

  const handlePickSession = useCallback(
    (sessionId: string) => {
      setHistoryOpen(false);
      voidCall(switchSession(sessionId));
    },
    [switchSession],
  );

  const handleNewSession = useCallback(() => {
    setHistoryOpen(false);
    createNewSession();
  }, [createNewSession]);

  return (
    <div className="relative isolate flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border px-2">
        <Button variant="ghost" size="icon-sm" onClick={toggleAssistantPanel} aria-label={t("dashboard:collapse_panel")}>
          <PanelRightClose aria-hidden />
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-2 px-1">
          <h2 className="min-w-0 text-sm font-medium">
            <TruncatedText text={title} />
          </h2>
          {isWorking && (
            <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden className="size-1.5 animate-breath rounded-full bg-primary" />
              {t("dashboard:thinking")}
            </span>
          )}
        </div>
        <Button
          variant={historyOpen ? "secondary" : "ghost"}
          size="icon-sm"
          aria-pressed={historyOpen}
          aria-label={t("dashboard:session_history_title")}
          onClick={() => setHistoryOpen((open) => !open)}
        >
          <History aria-hidden />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label={t("dashboard:new_session_action")} onClick={handleNewSession}>
          <Plus aria-hidden />
        </Button>
      </header>

      {historyOpen ? (
        <SessionHistory onPick={handlePickSession} onDelete={deleteSession} onClose={() => setHistoryOpen(false)} />
      ) : (
        <MessageFlow
          key={currentSessionId ?? "draft"}
          ref={flowRef}
          onSubmitEdit={handleSubmitEdit}
          onRetryStartup={startupFailureOrigin === "rewrite" ? undefined : () => composerRef.current?.send()}
        />
      )}

      <TodoProgress turns={turns} draftTurn={draftTurn} />
      {pendingQuestion && (
        <AgentQuestionnaire
          pendingQuestion={pendingQuestion}
          answering={answeringQuestion}
          error={error}
          onSubmit={handleSubmitAnswers}
        />
      )}

      {!pendingQuestion && <>
        {error && (
          <p role="alert" className="shrink-0 border-t border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}
        {currentProjectName && <div className="px-3"><RunSettings projectName={currentProjectName} kind="chat" disabled={isWorking} /></div>}
      </>}
      {!pendingQuestion && freeImageTask && freeImageTask.project === currentProjectName && (
        <FreeImageResult key={freeImageTask.task_id} projectName={currentProjectName} taskId={freeImageTask.task_id} draftId={freeImageTask.draft_id} />
      )}
      <AgentComposer
        ref={composerRef}
        hidden={Boolean(pendingQuestion)}
        disabled={inputDisabled}
        running={isRunning}
        sending={sending}
        placeholder={isRunning ? t("dashboard:generating_queue_hint") : t("dashboard:input_placeholder")}
        onSend={handleSend}
        onGenerateFreeImage={currentProjectName ? (prompt) => setFreeImageRequest({ project: currentProjectName, prompt }) : undefined}
        onInterrupt={() => voidCall(interrupt())}
        onWithdrawQueued={withdrawQueuedMessage}
        onResendQueued={handleResendQueued}
        onSendQueuedNow={handleSendQueuedNow}
      />
      {freeImageRequest && freeImageRequest.project === currentProjectName && (
        <FreeImageDialog
          projectName={currentProjectName}
          initialPrompt={freeImageRequest.prompt}
          onClose={() => setFreeImageRequest(null)}
          onSubmitted={(task) => { setFreeImageTask({ ...task, project: currentProjectName }); setFreeImageRequest(null); }}
        />
      )}
    </div>
  );
}
