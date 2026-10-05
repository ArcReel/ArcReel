import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { useAssistantStore } from "@/stores/assistant-store";
import { FakeSseStream } from "@/test/fakeSseStream";
import type { FailureObservation, SessionMeta, TimelineEntry } from "@/types";
import { MessageFlow } from "./MessageFlow";

const failure: FailureObservation = {
  version: 1,
  phase: "turn",
  timestamp: "2026-07-23T01:02:03Z",
  project_name: "demo",
  session_id: "session-1",
  summary: { key: "rate_limit", source: "sdk_assistant", type: "rate_limit", message: "429" },
  raw: {},
};

function makeSession(status: SessionMeta["status"]): SessionMeta {
  return {
    id: "session-1",
    project_name: "demo",
    title: "session-1",
    status,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  };
}

function userEntry(seq: number): TimelineEntry {
  return { seq, type: "user", content: [{ type: "text", text: `第 ${seq} 条` }], uuid: `u-${seq}` };
}

function failureEntry(seq: number): TimelineEntry {
  return { seq, type: "system", subtype: "agent_turn_failure", failure, uuid: `f-${seq}` };
}

function mockSession(status: SessionMeta["status"], entries: TimelineEntry[] = []) {
  vi.spyOn(API, "listAssistantSessions").mockResolvedValue({ sessions: [makeSession(status)] });
  vi.spyOn(API, "getAssistantSession").mockResolvedValue({ session: makeSession(status) });
  vi.spyOn(API, "listAssistantEntries").mockResolvedValue({
    session_id: "session-1",
    status,
    entries,
    draft: null,
    draft_rev: 0,
  });
}

function Panel() {
  useAssistantSession("demo");
  return <MessageFlow onSubmitEdit={vi.fn()} />;
}

describe("MessageFlow 失败卡片的播报", () => {
  beforeEach(() => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    FakeSseStream.reset();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(API, "openAssistantEntriesStream").mockImplementation((options) => new FakeSseStream(options.onEvent));
    vi.spyOn(API, "listAssistantSkills").mockResolvedValue({ skills: [] });
  });

  it("打开含失败卡片的历史会话时不播报", async () => {
    mockSession("idle", [userEntry(0), failureEntry(1)]);
    render(<Panel />);

    expect(await screen.findByRole("region", { name: "这一轮没有完成" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("运行中的会话经 entry 流补发的历史不播报，之后新到达的失败播报", async () => {
    mockSession("running");
    render(<Panel />);
    await waitFor(() => expect(FakeSseStream.instances).toHaveLength(1));
    const stream = FakeSseStream.instances[0];

    act(() => {
      stream.emit("entry", userEntry(0));
      stream.emit("entry", failureEntry(1));
      stream.emit("entry", userEntry(2));
      stream.emit("draft", { draft: null, rev: 0 });
    });
    expect(screen.getByRole("region", { name: "这一轮没有完成" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    act(() => stream.emit("entry", failureEntry(3)));
    expect(screen.getByRole("alert")).toHaveAccessibleName("这一轮没有完成");
    expect(screen.getAllByRole("region", { name: "这一轮没有完成" })).toHaveLength(1);
  });
});
