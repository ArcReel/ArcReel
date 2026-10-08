import { useImperativeHandle, useMemo, type Ref } from "react";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ImagePayload, TimelineEntry } from "@/types";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
  useMessageScroller,
} from "@/components/ui/message-scroller";
import { useAssistantStore } from "@/stores/assistant-store";
import { AgentFailureCard } from "./AgentFailureCard";
import { ArrivedFailuresContext } from "./arrived-failures";
import { buildDisplayItems } from "./display-items";
import { MessageRow } from "./MessageRow";
import { canEditUserTurn } from "./utils";

// ---------------------------------------------------------------------------
// MessageFlow — Agent 面板的消息区。
//
// 贴底时随新内容跟随到底；用户上翻即停止跟随，底部出现「跳到最新」。发送消息与
// 提交回答时由调用方经 MessageFlowHandle.scrollToEnd 显式滚到底，并恢复跟随。
// 切换会话时由调用方以会话 id 作 key 重新挂载，新会话从底部开始。
//
// 失败卡片只为查看期间新到达的失败播报（role="alert"）；打开会话时已有的失败
// 照常显示但不播报，否则载入一段历史就会连读几张失败卡片。新到达的集合经
// context 传给子智能体卡片，展开着的子时间线里新到达的失败同样播报。启动失败
// 只在本次发送时产生，总是新的。
// ---------------------------------------------------------------------------

export interface MessageFlowHandle {
  /** 滚到最新一条并恢复贴底跟随。 */
  scrollToEnd: () => void;
}

interface MessageFlowProps {
  ref?: Ref<MessageFlowHandle>;
  /** 提交原地编辑的改写。 */
  onSubmitEdit: (turnUuid: string, text: string, images: ImagePayload[]) => void;
  /** 启动失败卡片的「重试」；不传时卡片不给重试。 */
  onRetryStartup?: () => void;
}

export function MessageFlow(props: MessageFlowProps) {
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="end">
      <MessageFlowBody {...props} />
    </MessageScrollerProvider>
  );
}

function MessageFlowBody({ ref, onSubmitEdit, onRetryStartup }: MessageFlowProps) {
  const { t } = useTranslation("dashboard");
  const { scrollToEnd } = useMessageScroller();
  useImperativeHandle(ref, () => ({ scrollToEnd: () => void scrollToEnd() }), [scrollToEnd]);

  const turns = useAssistantStore((s) => s.turns);
  const draftTurn = useAssistantStore((s) => s.draftTurn);
  const messagesLoading = useAssistantStore((s) => s.messagesLoading);
  const editingTurnUuid = useAssistantStore((s) => s.editingTurnUuid);
  const setEditingTurnUuid = useAssistantStore((s) => s.setEditingTurnUuid);
  const sending = useAssistantStore((s) => s.sending);
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  const hasPendingQuestion = useAssistantStore((s) => Boolean(s.pendingQuestion));
  const hasQueuedMessages = useAssistantStore((s) => s.queuedMessages.length > 0);
  const startupFailure = useAssistantStore((s) => s.startupFailure);
  const entries = useAssistantStore((s) => s.entries);
  const historySeq = useAssistantStore((s) => s.historySeq);

  const items = useMemo(() => buildDisplayItems(turns, draftTurn), [turns, draftTurn]);
  const arrived = useMemo(() => arrivedFailures(entries, historySeq), [entries, historySeq]);

  if (items.length === 0 && !startupFailure) {
    // 加载期间留白，不闪一下空状态
    if (messagesLoading) return <div className="min-h-0 flex-1" />;
    return (
      <Empty className="min-h-0 flex-1">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Bot aria-hidden />
          </EmptyMedia>
          <EmptyTitle>{t("start_chat_hint")}</EmptyTitle>
          <EmptyDescription>{t("quick_skill_hint")}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <MessageScroller className="min-h-0 flex-1">
      <MessageScrollerViewport aria-label={t("chat_transcript_label")}>
        <MessageScrollerContent>
          <ArrivedFailuresContext.Provider value={arrived}>
            {items.map(({ key, turn, streaming }) => (
              <MessageScrollerItem key={key} messageId={key}>
                <MessageRow
                  turn={turn}
                  streaming={streaming}
                  announce={turn.uuid !== undefined && arrived.has(turn.uuid)}
                  editable={canEditUserTurn(turn, { sessionStatus, hasPendingQuestion, isSending: sending, hasQueuedMessages })}
                  editing={Boolean(turn.uuid) && turn.uuid === editingTurnUuid}
                  submitting={sending}
                  onStartEdit={setEditingTurnUuid}
                  onCancelEdit={() => setEditingTurnUuid(null)}
                  onSubmitEdit={onSubmitEdit}
                />
              </MessageScrollerItem>
            ))}
          </ArrivedFailuresContext.Provider>
          {startupFailure && (
            <MessageScrollerItem messageId="startup-failure">
              <AgentFailureCard failure={startupFailure} announce onRetry={onRetryStartup} />
            </MessageScrollerItem>
          )}
        </MessageScrollerContent>
      </MessageScrollerViewport>
      <MessageScrollerButton />
    </MessageScroller>
  );
}

/** 查看期间新到达的轮次失败条目的 uuid。条目按 seq 升序，从末尾往回找到历史边界为止。 */
function arrivedFailures(entries: TimelineEntry[], historySeq: number | null): ReadonlySet<string> {
  const uuids = new Set<string>();
  if (historySeq === null) return uuids;
  for (let i = entries.length - 1; i >= 0 && entries[i].seq > historySeq; i -= 1) {
    const entry = entries[i];
    if (entry.type === "system" && entry.subtype === "agent_turn_failure" && entry.uuid) uuids.add(entry.uuid);
  }
  return uuids;
}
