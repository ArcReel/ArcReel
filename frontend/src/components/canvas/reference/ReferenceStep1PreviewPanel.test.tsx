import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { ReferenceStep1PreviewPanel } from "./ReferenceStep1PreviewPanel";
import type { MentionLookup } from "@/hooks/useShotPromptHighlight";
import type { ScriptReviewState } from "@/types";

const LOOKUP: MentionLookup = { 阿离: "character", 长街: "scene" };

function pendingState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    content: {
      units: [
        {
          unit_id: "E1U01",
          shots: [{ text: "@[阿离] 撑伞走过 @[长街]" }],
          duration_seconds: 8,
          references: [
            { type: "character", name: "阿离" },
            { type: "scene", name: "长街" },
          ],
          source_text: "阿离撑伞走过长街。",
        },
      ],
    },
    ...overrides,
  };
}

function quarantinedState(): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: null,
    confirmed_at: null,
    content: null,
    quarantine: {
      content: {
        units: [
          {
            duration_seconds: 8,
            source_text: "阿离撑伞走过长街。",
            text: "镜头1：门开了\n@[阿离]：｛我来了。｝",
          },
        ],
      },
      violations: [
        { code: "fullwidth_braces", label: "unit E1U01", message: "unit E1U01 使用了全角花括号", line: 1 },
        { code: "dialogue_overload", label: "unit E1U01", message: "unit E1U01 的台词念不完", line: null },
      ],
    },
  };
}

describe("ReferenceStep1PreviewPanel", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the clean pending state with highlighted script and reference pills", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    render(<ReferenceStep1PreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());
    expect(screen.getByText("阿离撑伞走过长街。")).toBeInTheDocument();
    expect(screen.getAllByText("阿离").length).toBeGreaterThan(0);
    expect(screen.getAllByText("长街").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).not.toBeDisabled();
  });

  it("confirms, then prefills a continue message into the assistant input without sending", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(pendingState({ status: "confirmed", quarantine: null }));

    render(<ReferenceStep1PreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /确认拆分，继续生成/ }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("第 1 集"));
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("quarantined state anchors a line-level violation inline and aggregates the unit-level one, blocking confirm", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceStep1PreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());
    expect(screen.getByText("unit E1U01 使用了全角花括号")).toBeInTheDocument();
    expect(screen.getByText("unit E1U01 的台词念不完")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "让助手修复" })).toBeInTheDocument();
  });

  it("prefills a structured fix-request report on 'ask the assistant to fix it', without sending", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceStep1PreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "让助手修复" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "让助手修复" }));

    const input = useAssistantStore.getState().input;
    expect(input).toContain("第 1 集");
    expect(input).toContain("1. unit E1U01 使用了全角花括号");
    expect(input).toContain("2. unit E1U01 的台词念不完");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("shows an empty state when there is no step1 content", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "no_step1",
      fingerprint: null,
      confirmed_at: null,
      content: null,
      quarantine: null,
    });
    render(<ReferenceStep1PreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByText("暂无预处理内容")).toBeInTheDocument());
  });

  it("edits a shot in the non-quarantined state and persists the units draft", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(pendingState());

    render(<ReferenceStep1PreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("@[阿离] 撑伞走过 @[长街]");
    fireEvent.change(textarea, { target: { value: "@[阿离] 缓步走过 @[长街]" } });

    const saveBtn = await screen.findByText("保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      units: [{ unit_id: "E1U01", shots: [{ text: "@[阿离] 缓步走过 @[长街]" }] }],
    });
  });
});
