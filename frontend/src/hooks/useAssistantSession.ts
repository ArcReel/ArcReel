import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { errMsg, voidCall } from "@/utils/async";
import { AgentFailureError, API } from "@/api";
import { uid } from "@/utils/id";
import type { AttachedImage } from "@/hooks/useImageAttachments";
import { useAssistantStore } from "@/stores/assistant-store";
import type {
  DraftDeltaPayload,
  DraftState,
  ImagePayload,
  PendingQuestion,
  SessionMeta,
  TimelineEntry,
} from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseSsePayload(event: MessageEvent): Record<string, unknown> {
  try {
    return JSON.parse(String(event.data || "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

const TERMINAL = new Set(["completed", "error", "interrupted"]);

function lastEntrySeq(entries: TimelineEntry[]): number {
  return entries.length > 0 ? entries[entries.length - 1].seq : -1;
}

function deletingKey(projectName: string, sessionId: string): string {
  return `${projectName}\n${sessionId}`;
}

// ---------------------------------------------------------------------------
// localStorage helpers — 记住每个项目最后使用的会话
// ---------------------------------------------------------------------------

const LAST_SESSION_KEY = "arcreel:lastSessionByProject";

function getLastSessionId(projectName: string): string | null {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_SESSION_KEY) || "{}") as Record<string, unknown>;
    const value = map[projectName];
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}

function saveLastSessionId(projectName: string, sessionId: string): void {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_SESSION_KEY) || "{}") as Record<string, unknown>;
    map[projectName] = sessionId;
    localStorage.setItem(LAST_SESSION_KEY, JSON.stringify(map));
  } catch {
    // 静默失败
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * 管理 Agent 会话生命周期，时间线唯一读源为会话事件日志：
 * - 冷读 GET entries（历史回放）
 * - SSE entry 流实时接收（事件 id 即 seq，断线按游标续传）
 * - 发送消息：服务端先写日志分配身份，响应回传权威条目；
 *   client_key 幂等，重试不产生重复；不渲染本地合成消息
 */
export function useAssistantSession(projectName: string | null) {
  const { t } = useTranslation("dashboard");
  const store = useAssistantStore;
  const streamRef = useRef<EventSource | null>(null);
  const streamSessionRef = useRef<string | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusRef = useRef<string>("idle");
  const pendingSendVersionRef = useRef(0);
  // 失败重试复用同一幂等键（同内容签名），成功后清除
  const failedSendRef = useRef<{ clientKey: string; signature: string } | null>(null);
  const failedRewriteRef = useRef<{ clientKey: string; signature: string } | null>(null);

  const syncPendingQuestion = useCallback((question: PendingQuestion | null) => {
    store.getState().setPendingQuestion(question);
    store.getState().setAnsweringQuestion(false);
  }, [store]);

  const clearPendingQuestion = useCallback(() => {
    syncPendingQuestion(null);
  }, [syncPendingQuestion]);

  const invalidatePendingSend = useCallback(() => {
    pendingSendVersionRef.current += 1;
    store.getState().setSending(false);
  }, [store]);

  // 会话加载链取消域：init 的会话自动选择与 loadSession（冷读/建流）共用一个
  // controller。任何接管会话选择权的入口（切会话/新建/删除/发送建会话/项目切换）
  // 先作废在途链，迟到响应经 signal 短路，不写 store、不建 SSE 连接。
  const loadAbortRef = useRef<AbortController | null>(null);
  // 项目级取消域的当前 controller：跨 useCallback 边界（connectStream 的终态刷新）
  // 复用同一 signal，随项目切换/卸载 abort。
  const projectAbortRef = useRef<AbortController | null>(null);
  // 当前 controller 属于哪个项目。回调可能来自上一个项目的迟到收尾，那时
  // projectAbortRef 里装的已经是新项目的 controller，只看 signal 认不出跨项目。
  const projectAbortOwnerRef = useRef<string | null>(null);

  const abortSessionLoad = useCallback(() => {
    loadAbortRef.current?.abort();
    loadAbortRef.current = null;
  }, []);

  const beginSessionLoad = useCallback(() => {
    abortSessionLoad();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    return controller.signal;
  }, [abortSessionLoad]);

  // 会话列表的本地权威写入代数。新建/改写分叉/删除都在本地即时改写列表，这类写入
  // 表达的是「服务端已经这么做了」，比任何更早开始的读都新；代数在写入时递增，读到
  // 的旧列表据此作废，不会把已消失的会话写回来、把新分支挤掉。
  const sessionsWriteVersionRef = useRef(0);
  // 最近一次成功加载完成的会话；加载失败后为 null，用于放行对同一会话的重试
  const loadedSessionRef = useRef<string | null>(null);
  // 在途的「删除当前会话」计数，按项目 + 会话分桶。改写的列表补拉只在它的源会话
  // 正被删除时跳过：补拉会把分支拉进列表，那次删除收尾就顺手切到它，正是进入时
  // 那次作废要避免的结果。分桶到会话、且计数而非布尔，别处的删除才不会连坐——
  // 共享一个格子时，并行的另一次删除收尾会替这一次把保护摘掉，别的项目/会话上
  // 迟迟不返回的删除则会一直压着本该发生的补拉，新分支不刷新页面就找不回来。
  const deletingCurrentRef = useRef<Record<string, number>>({});
  // 因上面的保护而没做成的改写补拉，按同一个 key 记账，删除收尾后补上：服务端此刻
  // 已开出新分支，两条删除路径都只处理原会话，不补拉的话新分支要等刷新页面才出现。
  const deferredRefreshRef = useRef<Set<string>>(new Set());

  const writeSessions = useCallback((sessions: SessionMeta[]) => {
    // 上一个项目的迟到回调既不写列表也不占代数：占了代数，新项目会把自己刚拉到的
    // 初始列表误判成过期而丢弃，侧边栏就一直停在旧项目的会话上
    if (projectAbortOwnerRef.current !== projectName) return;
    sessionsWriteVersionRef.current += 1;
    // 面板是长生命周期单例，切项目后列表在新项目的响应到达前仍停在上一个项目：合并
    // 时滤掉外来会话，否则新建/改写会把它们一起固化进来，而本项目在途的权威列表又
    // 因为这次写入被判过期丢弃，侧边栏就一直挂着一串在本项目里打不开的会话
    store.getState().setSessions(sessions.filter((s) => s.project_name === projectName));
  }, [projectName, store]);

  // 补拉会话列表。纳入项目级取消域，挂起期间切换项目时迟到响应不得覆盖已切到的
  // 新项目会话列表；空列表不写入，避免一次失败的读把列表清空。
  const refreshSessions = useCallback(() => {
    if (!projectName) return;
    // 本次补拉属于 projectName，取消域也必须是它的。改写的迟到收尾会带着旧
    // projectName 走到这里，此刻 ref 里已是新项目的 controller——借用它去读旧项目
    // 的列表，再把结果写进新项目的侧边栏，用户会看到一串打不开的会话。
    if (projectAbortOwnerRef.current !== projectName) return;
    const signal = projectAbortRef.current?.signal;
    if (!signal) return;
    const writeVersion = sessionsWriteVersionRef.current;
    API.listAssistantSessions(projectName, null, { signal })
      .then((res) => {
        if (signal.aborted) return;
        if (projectAbortOwnerRef.current !== projectName) return;
        // 本次读开始之后发生过本地权威写入：这份列表已经过期
        if (sessionsWriteVersionRef.current !== writeVersion) return;
        const fresh = res.sessions ?? [];
        if (fresh.length > 0) store.getState().setSessions(fresh);
      })
      .catch(() => {/* 静默失败 */});
  }, [projectName, store]);

  // 改写被作废后的列表对账。源会话正在删除时不能当场补拉——补拉会把新分支带进列表，
  // 那次删除收尾就顺手切到它，正是作废要避免的结果；改为记账，等删除收尾后再补。
  const reconcileAfterStaleRewrite = useCallback((originSessionId: string) => {
    if (!projectName) return;
    const key = deletingKey(projectName, originSessionId);
    if ((deletingCurrentRef.current[key] ?? 0) > 0) {
      deferredRefreshRef.current.add(key);
      return;
    }
    refreshSessions();
  }, [projectName, refreshSessions]);

  // 关闭流
  const closeStream = useCallback(() => {
    if (reconnectRef.current) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    streamSessionRef.current = null;
  }, []);

  // 连接 SSE entry 流
  const connectStream = useCallback(
    (sessionId: string) => {
      // 如果已连接到同一 session 且连接健康，跳过重连
      if (
        streamRef.current &&
        streamSessionRef.current === sessionId &&
        streamRef.current.readyState !== EventSource.CLOSED
      ) {
        return;
      }

      closeStream();
      streamSessionRef.current = sessionId;

      // 冷订阅游标：已有条目之后；浏览器自动重连由 Last-Event-ID 续传
      const after = lastEntrySeq(store.getState().entries);
      const url = API.getAssistantEntriesStreamUrl(projectName!, sessionId, after);
      const source = new EventSource(url);
      streamRef.current = source;
      const isActiveStream = () =>
        streamRef.current === source &&
        streamSessionRef.current === sessionId &&
        store.getState().currentSessionId === sessionId;

      source.addEventListener("entry", (event) => {
        if (!isActiveStream()) return;
        const entry = parseSsePayload(event);
        if (typeof entry.seq === "number" && typeof entry.type === "string") {
          store.getState().appendEntry(entry as unknown as TimelineEntry);
        }
      });

      source.addEventListener("draft", (event) => {
        if (!isActiveStream()) return;
        const payload = parseSsePayload(event);
        const draft = (payload.draft ?? null) as DraftState | null;
        const rev = typeof payload.rev === "number" ? payload.rev : 0;
        store.getState().setDraftSnapshot(draft, rev);
      });

      source.addEventListener("delta", (event) => {
        if (!isActiveStream()) return;
        const payload = parseSsePayload(event);
        if (typeof payload.message_id === "string" && typeof payload.rev === "number") {
          store.getState().applyDelta(payload as unknown as DraftDeltaPayload);
        }
      });

      source.addEventListener("status", (event) => {
        if (!isActiveStream()) return;
        const data = parseSsePayload(event);
        const status = (data.status as string) ?? statusRef.current;

        statusRef.current = status;
        store.getState().setSessionStatus(status as "idle");

        if (TERMINAL.has(status)) {
          store.getState().setSending(false);
          store.getState().setInterrupting(false);
          clearPendingQuestion();
          // 中断时保留 draft：被中断的流式内容不入日志，刷新后自然消失
          if (status !== "interrupted") {
            store.getState().clearDraft();
          }
          closeStream();

          // Turn 结束后刷新会话列表，获取 SDK summary 标题
          refreshSessions();
        }
      });

      source.addEventListener("question", (event) => {
        if (!isActiveStream()) return;
        const payload = parseSsePayload(event);
        const pendingQuestion = getPendingQuestionFromEvent(payload);
        if (pendingQuestion) {
          syncPendingQuestion(pendingQuestion);
        }
      });

      source.onerror = () => {
        if (!isActiveStream()) return;
        // 浏览器原生自动重连携带 Last-Event-ID 续传；此处仅兜底
        // 连接被判死（CLOSED）的场景，运行中或发送中才重建。
        if (
          source.readyState === EventSource.CLOSED &&
          (statusRef.current === "running" || store.getState().sending)
        ) {
          reconnectRef.current = setTimeout(() => {
            // 自引用 SSE 重连：useEffectEvent 不允许在 setTimeout 内调用，
            // 用 ref 中转又被 immutability 规则禁止。当前写法是延迟到下一 tick
            // 才执行，闭包内的 connectStream 引用已稳定，行为正确。
            // eslint-disable-next-line react-hooks/immutability
            connectStream(sessionId);
          }, 3000);
        }
      };
    },
    [clearPendingQuestion, projectName, closeStream, refreshSessions, store, syncPendingQuestion],
  );

  // 加载指定会话时间线：非 running 冷读日志；running 交给 entry 流回放。
  // signal 被 abort 时网络 await 断点由 fetch 自动 reject；写 store 与建流前
  // 复核 aborted，拦截「abort 发生在响应已 resolve 之后」的窗口。
  const loadSession = useCallback(async (sessionId: string, options: { signal: AbortSignal }) => {
    const { signal } = options;
    const res = await API.getAssistantSession(projectName!, sessionId, { signal });
    if (signal.aborted) return;
    const raw = res as Record<string, unknown>;
    const sessionObj = (raw.session ?? raw) as Record<string, unknown>;
    const status = (sessionObj.status as string) ?? "idle";
    statusRef.current = status;
    store.getState().setSessionStatus(status as "idle");
    // 清掉跨挂载残留的过期问题（zustand 全局 store 在组件卸载后仍保留）；
    // running 会话的未决问题由 entry 流的 question 事件重新投递。
    clearPendingQuestion();

    if (status === "running") {
      connectStream(sessionId);
    } else {
      const data = await API.listAssistantEntries(projectName!, sessionId, -1, { signal });
      if (signal.aborted) return;
      store.getState().setEntries(data.entries ?? []);
      store.getState().setDraftSnapshot(data.draft ?? null, data.draft_rev ?? 0);
    }
  }, [projectName, clearPendingQuestion, connectStream, store]);

  // 加载会话
  useEffect(() => {
    if (!projectName) {
      // 离开项目：上一个 projectName 的 cleanup 已 abort 在途加载链，但没有
      // 后续加载链接管收尾——显式清掉可能遗留的 loading，避免卡死为 true
      store.getState().setMessagesLoading(false);
      return;
    }
    // 项目级取消域：只随项目切换/卸载 abort。会话列表与技能列表是项目级数据，
    // 挂起期间的会话操作（新建/切换/删除）不应作废它们——否则慢响应下技能列表
    // 会被误判过期丢弃、新项目的会话列表被陈旧会话点击作废且无重试。
    const projectAbort = new AbortController();
    projectAbortRef.current = projectAbort;
    projectAbortOwnerRef.current = projectName;
    // 加载完成标记按项目作废：留着上一个项目的会话 id，回到那个项目后若自动重载
    // 失败，点它会被短路当成「已加载」，重试就永远进不来
    loadedSessionRef.current = null;

    async function init() {
      // 会话自动选择占据加载链：后续任何用户会话操作接管选择权时作废
      const loadSignal = beginSessionLoad();
      store.getState().setMessagesLoading(true);
      // 切项目先重置时间线（与新建/切换/删除三条会话路径同口径），使有会话/
      // 无会话两个分支都从干净状态出发：running 会话的 SSE 冷订阅游标由重置后
      // 的空 entries 推导（等效从头订阅），不被上一个项目的残留条目污染，也不会
      // 把旧项目条目混排进新会话时间线。
      store.getState().resetTimeline();
      const writeVersion = sessionsWriteVersionRef.current;
      try {
        // 获取会话列表（项目级数据：即便自动选择已被用户操作作废，列表照常落地）
        const res = await API.listAssistantSessions(projectName!, null, { signal: projectAbort.signal });
        if (projectAbort.signal.aborted) return;
        const sessions = res.sessions ?? [];
        // 这份读期间发生过本地权威写入（建会话/改写分叉/删会话）就已过期，整份
        // 作废——不只是别写列表：删掉的若不是当前会话，加载链不会被作废，照着这份
        // 名单自动选择会选中一条已经不存在的会话并去加载它的时间线
        if (sessionsWriteVersionRef.current !== writeVersion) return;
        store.getState().setSessions(sessions);
        if (loadSignal.aborted) return;

        // 优先使用上次选择的会话（如果仍存在于列表中）
        const lastId = getLastSessionId(projectName!);
        const sessionId = (lastId && sessions.some((s: SessionMeta) => s.id === lastId))
          ? lastId
          : sessions[0]?.id;
        if (!sessionId) {
          store.getState().setCurrentSessionId(null);
          clearPendingQuestion();
          store.getState().setMessagesLoading(false);
          return;
        }

        store.getState().setCurrentSessionId(sessionId);
        await loadSession(sessionId, { signal: loadSignal });
        // 与 switchSession 同口径地记账：不记的话，点一下本来就选中的这条会话不会
        // 被短路，时间线白重建一次，连带丢掉还没提交的编辑草稿
        if (!loadSignal.aborted) loadedSessionRef.current = sessionId;
      } catch {
        // 静默失败（含被 abort 中止的请求）
      } finally {
        // 被作废时 loading 归接管方管理（switchSession 自开自收、
        // createNewSession / deleteSession / sendMessage 显式复位），
        // 此处复位会踩到接管方正在进行的加载
        if (!loadSignal.aborted) store.getState().setMessagesLoading(false);
      }
    }

    // 加载技能列表
    API.listAssistantSkills(projectName, { signal: projectAbort.signal })
      .then((res) => {
        if (projectAbort.signal.aborted) return;
        store.getState().setSkills(res.skills ?? []);
      })
      .catch(() => {});

    voidCall(init());

    return () => {
      projectAbort.abort();
      if (projectAbortRef.current === projectAbort) {
        projectAbortRef.current = null;
        projectAbortOwnerRef.current = null;
        loadedSessionRef.current = null;
      }
      abortSessionLoad();
      invalidatePendingSend();
      closeStream();
    };
  }, [
    projectName,
    abortSessionLoad,
    beginSessionLoad,
    clearPendingQuestion,
    closeStream,
    invalidatePendingSend,
    loadSession,
    store,
    writeSessions,
  ]);

  // 发送消息。返回是否受理成功——失败时调用方保留输入内容。
  const sendMessage = useCallback(
    async (content: string, images?: AttachedImage[]): Promise<boolean> => {
      if ((!content.trim() && (!images || images.length === 0)) || store.getState().sending) {
        return false;
      }

      const sendVersion = pendingSendVersionRef.current + 1;
      pendingSendVersionRef.current = sendVersion;
      let sessionId = store.getState().currentSessionId;
      store.getState().setSending(true);
      store.getState().setError(null);
      store.getState().setStartupFailure(null);

      // 提取 base64 数据
      const imagePayload = images?.map((img) => ({
        data: img.dataUrl.split(",")[1] ?? "",
        media_type: img.mimeType,
      }));

      // 请求侧幂等键：同一内容失败重试复用同键，服务端按键去重不产生重复。
      // 签名纳入 projectName：面板为长生命周期单例，切换项目不卸载，失败缓存
      // （clientKey + 签名）跨项目存活；含项目维度后旧项目缓存的 clientKey 自然
      // 失效，不再被其他项目的同内容重发复用。
      const signature = JSON.stringify([projectName, sessionId, content.trim(), imagePayload ?? []]);
      const clientKey =
        failedSendRef.current?.signature === signature
          ? failedSendRef.current.clientKey
          : uid();

      try {
        const result = await API.sendAssistantMessage(
          projectName!,
          content,
          sessionId,  // null for new session
          imagePayload,
          clientKey,
        );

        if (pendingSendVersionRef.current !== sendVersion) return false;
        failedSendRef.current = null;
        // 发送已受理：用户以发送行为接管会话选择权与时间线，作废在途加载链
        // （init 自动选择不再覆盖 currentSessionId，挂起的冷读不再覆写时间线），
        // 并复位其遗留的 loading
        abortSessionLoad();
        store.getState().setMessagesLoading(false);

        const returnedSessionId = result.session_id;

        // 新会话：更新 store
        if (!sessionId) {
          const newSession: SessionMeta = {
            id: returnedSessionId,
            project_name: projectName!,
            title: content.trim().slice(0, 30) || "图片消息",
            status: "running",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
          store.getState().setCurrentSessionId(returnedSessionId);
          writeSessions([newSession, ...store.getState().sessions]);
          store.getState().setIsDraftSession(false);
          saveLastSessionId(projectName!, returnedSessionId);
          sessionId = returnedSessionId;
        }

        if (store.getState().currentSessionId !== sessionId) return false;

        // 响应携带的权威条目（服务端已写日志分配身份），seq 门槛去重
        if (result.entry) {
          const lastSeq = lastEntrySeq(store.getState().entries);
          if (result.entry.seq > lastSeq + 1) {
            // seq 跳档：其他客户端在本地未订阅期间产生了轮次，先冷读补齐缺口，
            // 否则订阅游标越过缺口后中间条目永远不会被拉取
            try {
              const gap = await API.listAssistantEntries(projectName!, sessionId, lastSeq);
              if (store.getState().currentSessionId !== sessionId) return false;
              store.getState().setEntries(gap.entries ?? []);
            } catch {
              // 静默失败：缺口留待刷新兜底
            }
          }
          store.getState().appendEntry(result.entry);
        }
        statusRef.current = "running";
        store.getState().setSessionStatus("running");
        store.getState().setSending(false);
        connectStream(sessionId);
        return true;
      } catch (err) {
        if (pendingSendVersionRef.current !== sendVersion) return false;
        // 失败：无本地合成消息可回滚，仅记录幂等键供重试复用；
        // 在途加载链未被作废，挂起的冷读继续正常落地
        failedSendRef.current = { clientKey, signature };
        if (err instanceof AgentFailureError) {
          store.getState().setStartupFailure(err.failure);
        } else {
          store.getState().setError(errMsg(err, "发送失败"));
        }
        store.getState().setSending(false);
        return false;
      }
    },
    [projectName, abortSessionLoad, connectStream, store, writeSessions],
  );

  const answerQuestion = useCallback(
    async (questionId: string, answers: Record<string, string>) => {
      const sessionId = store.getState().currentSessionId;
      if (!projectName || !sessionId) return;

      store.getState().setError(null);
      store.getState().setAnsweringQuestion(true);

      try {
        await API.answerAssistantQuestion(projectName, sessionId, questionId, answers);
        store.getState().setPendingQuestion(null);
      } catch (err) {
        store.getState().setError(errMsg(err, "回答失败"));
      } finally {
        store.getState().setAnsweringQuestion(false);
      }
    },
    [projectName, store],
  );

  // 中断会话
  const interrupt = useCallback(async () => {
    const sessionId = store.getState().currentSessionId;
    if (!projectName || !sessionId || statusRef.current !== "running") return;

    store.getState().setInterrupting(true);
    try {
      await API.interruptAssistantSession(projectName, sessionId);
    } catch (err) {
      store.getState().setError(errMsg(err, "中断失败"));
      store.getState().setInterrupting(false);
    }
  }, [projectName, store]);

  // 创建新会话（懒创建：仅清空状态，实际创建延迟到首次发消息时）
  const createNewSession = useCallback(() => {
    if (!projectName) return;

    abortSessionLoad();
    invalidatePendingSend();
    closeStream();
    store.getState().resetTimeline();
    store.getState().setSessionStatus("idle");
    clearPendingQuestion();
    store.getState().setCurrentSessionId(null);
    store.getState().setIsDraftSession(true);
    // 草稿会话无加载过程，复位被作废加载链遗留的 loading
    store.getState().setMessagesLoading(false);
    statusRef.current = "idle";
  }, [projectName, abortSessionLoad, clearPendingQuestion, closeStream, invalidatePendingSend, store]);

  // 切换到指定会话
  const switchSession = useCallback(async (sessionId: string) => {
    // 面板为长生命周期单例：切项目为 null 后 sessions 列表不清空，仍可能渲染出
    // 旧项目的会话项，点击不得以 null projectName 发起请求
    if (!projectName) return;
    // 上一次加载失败过的会话不短路：否则界面停在空时间线、无 SSE，再点同一条也进不来
    if (store.getState().currentSessionId === sessionId && loadedSessionRef.current === sessionId) return;

    const signal = beginSessionLoad();
    invalidatePendingSend();
    closeStream();
    store.getState().setCurrentSessionId(sessionId);
    store.getState().setIsDraftSession(false);
    store.getState().resetTimeline();
    clearPendingQuestion();
    // 上一次加载失败的提示到此为止：重试成功后不该继续压在已经载入的对话上面
    store.getState().setError(null);
    store.getState().setMessagesLoading(true);

    // 记住选择
    saveLastSessionId(projectName, sessionId);

    loadedSessionRef.current = null;
    try {
      await loadSession(sessionId, { signal });
      if (!signal.aborted) loadedSessionRef.current = sessionId;
    } catch {
      // 被后续切换 abort 不是失败，接管方会自己收尾；真失败要说出来，
      // 否则用户面对的是一片空白的时间线，也不知道可以重试
      if (!signal.aborted) store.getState().setError(t("session_load_failed"));
    } finally {
      // 被作废时 loading 归接管方管理，此处复位会踩到其正在进行的加载
      if (!signal.aborted) store.getState().setMessagesLoading(false);
    }
  }, [projectName, beginSessionLoad, clearPendingQuestion, closeStream, invalidatePendingSend, loadSession, store, t]);

  // 改写历史用户消息。返回是否受理成功——失败时调用方保留编辑态与草稿内容。
  //
  // 受理成功前不动任何视图状态：改写会被服务端拒绝（锚点失效 400、未决问答或
  // 原会话已被取代 409），被拒时用户仍留在原会话，时间线原样。受理之后才整体
  // 切换到承接改写的新会话——时间线重建、SSE 重连，不做增量缝合。
  const rewriteMessage = useCallback(
    async (anchorEntryUuid: string, content: string, images?: ImagePayload[]): Promise<boolean> => {
      const originSessionId = store.getState().currentSessionId;
      if (!projectName || !originSessionId) return false;
      // 与发送同口径：图片附件本身就是内容，正文被改空的带图消息仍可提交
      if (!content.trim() && (!images || images.length === 0)) return false;
      // sending 同时是发送与改写的在途锁：两者都会开启新一轮，不能并发
      if (store.getState().sending) return false;

      // 与发送共用一轮版本号：任何接管会话选择权的入口都会 invalidatePendingSend，
      // 在途改写随之作废，迟到的响应不再把用户从他已切去的会话拽回分支
      const rewriteVersion = pendingSendVersionRef.current + 1;
      pendingSendVersionRef.current = rewriteVersion;
      store.getState().setSending(true);
      store.getState().setError(null);
      store.getState().setStartupFailure(null);

      // 幂等键：响应丢失后的重试由服务端在新分支里认领同一条权威条目，
      // 不会再分叉一次。签名含锚点与改写后内容（含附件），改了内容即换新键。
      const signature = JSON.stringify([
        projectName,
        originSessionId,
        anchorEntryUuid,
        content.trim(),
        images ?? [],
      ]);
      const clientKey =
        failedRewriteRef.current?.signature === signature
          ? failedRewriteRef.current.clientKey
          : uid();

      try {
        const result = await API.rewriteAssistantMessage(
          projectName,
          originSessionId,
          anchorEntryUuid,
          content,
          images,
          clientKey,
        );

        // 本轮已被作废：不把用户从他已切去的会话拽回分支，sending 也由作废方复位。
        // 但服务端此刻确已取代原会话并开出新分支，本地列表仍指向已消失的原会话——
        // 补拉一次列表，否则新分支要等到刷新页面才出现。
        if (pendingSendVersionRef.current !== rewriteVersion) {
          reconcileAfterStaleRewrite(originSessionId);
          return false;
        }
        failedRewriteRef.current = null;

        const newSessionId = result.session_id;
        // 原会话已被取代，服务端列表不再返回它；本地列表同步剔除，由新分支接位。
        // 标题沿用原会话（分支是同一段对话的续写），轮次结束时的列表刷新会用
        // SDK summary 校正。
        const sessions = store.getState().sessions;
        const origin = sessions.find((s) => s.id === originSessionId);
        const branch: SessionMeta = {
          id: newSessionId,
          project_name: projectName,
          title: origin?.title ?? "",
          status: "running",
          created_at: origin?.created_at ?? new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        writeSessions([
          branch,
          ...sessions.filter((s) => s.id !== originSessionId && s.id !== newSessionId),
        ]);
        store.getState().setSending(false);

        // 整体切到新分支：重置时间线（编辑态随之清空）、冷读/建流、记住选择，
        // 刷新与断线重连后都停在新分支
        await switchSession(newSessionId);
        return true;
      } catch (err) {
        // 幂等键无论本轮是否被作废都要留住：网络中断说不清服务端受理没有，重试时
        // 换新键才会真的分叉两次。作废时同样对账一次列表，受理了的话新分支就不必
        // 等到刷新页面
        failedRewriteRef.current = { clientKey, signature };
        if (pendingSendVersionRef.current !== rewriteVersion) {
          reconcileAfterStaleRewrite(originSessionId);
          return false;
        }
        // 启动失败与发送路径同口径，落故障卡片而非一行错误条。标注来源为改写：
        // 保留原始输入的是仍开着的编辑器，重放要由它的「重新发送」发起
        if (err instanceof AgentFailureError) {
          store.getState().setStartupFailure(err.failure, "rewrite");
        } else {
          store.getState().setError(errMsg(err, t("message_rewrite_failed")));
        }
        store.getState().setSending(false);
        return false;
      }
    },
    [projectName, reconcileAfterStaleRewrite, switchSession, store, t, writeSessions],
  );

  // 删除会话
  const deleteSession = useCallback(async (sessionId: string) => {
    if (!projectName) return;
    // 删除当前会话即刻接管会话选择权，作废不能等到 DELETE 返回：在途的发送/改写
    // 若在这期间被受理，会把用户装到一个分支上，而删除收尾此时已看不出该切换
    const invalidatedForDelete = store.getState().currentSessionId === sessionId;
    const deleteKey = deletingKey(projectName, sessionId);
    if (invalidatedForDelete) {
      invalidatePendingSend();
      deletingCurrentRef.current[deleteKey] = (deletingCurrentRef.current[deleteKey] ?? 0) + 1;
    }
    try {
      await API.deleteAssistantSession(projectName, sessionId);
      const sessions = store.getState().sessions.filter((s) => s.id !== sessionId);
      writeSessions(sessions);

      // 如果删除的是当前会话，切换到下一个
      if (store.getState().currentSessionId === sessionId) {
        if (sessions.length > 0) {
          await switchSession(sessions[0].id);
        } else {
          abortSessionLoad();
          invalidatePendingSend();
          closeStream();
          store.getState().setCurrentSessionId(null);
          store.getState().resetTimeline();
          store.getState().setSessionStatus(null);
          clearPendingQuestion();
          // 清空到无会话后无加载过程，复位被作废加载链遗留的 loading
          store.getState().setMessagesLoading(false);
          statusRef.current = "idle";
        }
      }
    } catch {
      // 删除没成功，会话还在。进入时那次作废已经把在途发送/改写的收尾摘掉了——
      // 若它其实被服务端受理了，这一轮此刻在本地不可见，输入框里的内容还会被再发
      // 一次。作废与补偿成对出现：重新加载一次当前会话，把它接回来。
      // 补偿的对象只能是被删的那一条：期间用户切走或换了项目的话，当前会话另有其人，
      // 拿闭包里的旧 projectName 去加载它只会把接管方的时间线冲掉
      if (invalidatedForDelete && store.getState().currentSessionId === sessionId
        && projectAbortOwnerRef.current === projectName) {
        loadedSessionRef.current = null;
        await switchSession(sessionId);
      }
    } finally {
      if (invalidatedForDelete) {
        deletingCurrentRef.current[deleteKey] -= 1;
        // 最后一次并行删除收尾后才补：早于此仍在删除窗口内
        if (deletingCurrentRef.current[deleteKey] === 0 && deferredRefreshRef.current.delete(deleteKey)) {
          refreshSessions();
        }
      }
    }
  }, [
    projectName,
    abortSessionLoad,
    clearPendingQuestion,
    closeStream,
    invalidatePendingSend,
    refreshSessions,
    switchSession,
    store,
    writeSessions,
  ]);

  return { sendMessage, rewriteMessage, answerQuestion, interrupt, createNewSession, switchSession, deleteSession };
}

function getPendingQuestionFromEvent(payload: Record<string, unknown>): PendingQuestion | null {
  if (!(typeof payload.question_id === "string" && Array.isArray(payload.questions))) {
    return null;
  }

  return {
    question_id: payload.question_id,
    questions: payload.questions as PendingQuestion["questions"],
  };
}
