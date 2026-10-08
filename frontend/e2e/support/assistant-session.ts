// 空闲 Agent 会话的接口替换：冷读的时间线，加上打开会话后建立的常驻 entry 流。
import type { ApiOverrides } from "./test.ts";

/**
 * 冷读返回 `entries`，常驻流从最后一条之后续读。流按服务端的开场帧回放 draft、空的排队消息快照与
 * idle 状态后结束；
 * retry 拉长重连间隔，测试期间不会重连。
 */
export function idleSessionApi(sessionsPath: string, sessionId: string, entries: { seq: number }[]): ApiOverrides {
  const after = entries.length > 0 ? entries[entries.length - 1].seq : -1;
  const stream = [
    "retry: 30000",
    "",
    "event: draft",
    `data: ${JSON.stringify({ session_id: sessionId, draft: null, rev: 0 })}`,
    "",
    "event: queue",
    `data: ${JSON.stringify({ session_id: sessionId, messages: [] })}`,
    "",
    "event: status",
    `data: ${JSON.stringify({ session_id: sessionId, status: "idle" })}`,
    "",
    "",
  ].join("\n");
  return {
    [`GET ${sessionsPath}/${sessionId}/entries`]: {
      status: 200,
      body: { session_id: sessionId, status: "idle", entries, draft: null, draft_rev: 0 },
    },
    [`GET ${sessionsPath}/${sessionId}/entries/stream?after=${after}`]: { status: 200, body: stream },
  };
}
