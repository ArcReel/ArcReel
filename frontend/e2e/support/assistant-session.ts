// Idle Agent session fixtures: cold-read timelines plus persistent entry streams opened after sessions.
import type { ApiOverrides } from "./test.ts";

/**
 * Cold reads return `entries`; persistent streams continue after last entries. Replay server opening frames with drafts, empty queued-message snapshots and
 * idle states, then end;
 * retry extends reconnect intervals, avoiding reconnects during tests.
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
