import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ScriptReviewGate } from "./ScriptReviewGate";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import type { ScriptReviewState } from "@/types";

function dramaState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "drama",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: null,
    duration_tiers: null,
    content: {
      title: "第一集",
      scenes: [
        {
          scene_id: "E1S01",
          duration_seconds: 8,
          segment_break: false,
          characters_in_scene: ["阿离"],
          scenes: [],
          props: [],
          scene_description: "雨夜，阿离立于屋檐下",
          utterances: [
            { kind: "voiceover", speaker: null, text: "三年后。" },
            { kind: "dialogue", speaker: "阿离", text: "你终于回来了。" },
          ],
          source_text: "三年后，阿离立于屋檐下：你终于回来了。",
        },
      ],
    },
    ...overrides,
  };
}

function narrationState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: null,
    duration_tiers: null,
    content: {
      segments: [
        {
          segment_id: "E1S01",
          novel_text: "裴与出征后的第二年。",
          duration_seconds: 6,
          segment_break: false,
          characters_in_segment: ["裴与"],
          scenes: [],
          props: [],
        },
      ],
    },
    ...overrides,
  };
}

describe("ScriptReviewGate", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders drama structured content with utterances and pending status", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());
    expect(screen.getByDisplayValue("阿离")).toBeInTheDocument();
    expect(screen.getByText("E1S01")).toBeInTheDocument();
    expect(screen.getByText("待确认")).toBeInTheDocument();
    expect(screen.getByText("确认并继续")).toBeInTheDocument();
  });

  it("confirms and reflects the unlocked state", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(dramaState({ status: "confirmed", confirmed_at: "2026-06-26T00:00:00Z" }));

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("确认并继续")).toBeInTheDocument());

    fireEvent.click(screen.getByText("确认并继续"));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1));
    await waitFor(() =>
      expect(screen.getByText("视觉生成已放行。再次编辑将重新等待确认。")).toBeInTheDocument(),
    );
  });

  it("edits content, surfaces save, and persists the edited intermediate", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(dramaState());

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("你终于回来了。"), { target: { value: "你怎么才回来。" } });
    // 编辑后出现保存按钮
    const saveBtn = await screen.findByText("修复后保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent, baseFingerprint] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      scenes: [{ utterances: [{ text: "三年后。" }, { text: "你怎么才回来。" }] }],
    });
    // 保存携带 GET 时拿到的内容指纹，供服务端做并发编辑冲突比对
    expect(baseFingerprint).toBe("fp1");
  });

  it("renders narration novel_text as editable", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByDisplayValue("裴与出征后的第二年。")).toBeInTheDocument());
    expect(screen.getByText("E1S01")).toBeInTheDocument();
  });

  it("locks the panel and lists violations when a draft needs fixes", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      narrationState({
        quarantine: {
          content: { segments: [{ segment_id: "E1S01", novel_text: "改到一半的原文。", duration_seconds: 5 }] },
          violations: [
            {
              code: "duration_off_tier",
              label: "segment E1S01",
              message: "segment E1S01 的时长 5 不在模型档位 [4, 6, 8] 内",
              line: null,
            },
          ],
        },
      }),
    );
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByText("待修复草稿 — 内容未通过校验")).toBeInTheDocument());
    // 违约逐条呈现，带定位前缀；正式内容不再可编辑，确认被锁。
    expect(screen.getByText("segment E1S01")).toBeInTheDocument();
    expect(screen.getByText(/不在模型档位/)).toBeInTheDocument();
    expect(screen.getByText("待修复项（1）")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("裴与出征后的第二年。")).not.toBeInTheDocument();
    expect(screen.getByText("确认并继续").closest("button")).toBeDisabled();

    // 「让 Agent 修复」把逐条违约预填进对话输入框、并把对话面板打开：用户不必自己把报告
    // 转述给 Agent。面板默认就是开着的，先关掉才断得出这次点击真的打开了它。
    act(() => useAppStore.getState().setAssistantPanelOpen(false));
    fireEvent.click(screen.getByText("让 Agent 修复"));
    const input = useAssistantStore.getState().input;
    expect(input).toContain("1 处违约待修复");
    expect(input).toContain("1. segment E1S01 的时长 5 不在模型档位 [4, 6, 8] 内");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("asks the assistant to promote instead of listing violations when the draft has none", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      dramaState({ quarantine: { content: { title: "第一集", scenes: [] }, violations: [] } }),
    );
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("待修复草稿 — 内容未通过校验")).toBeInTheDocument());
    expect(screen.getByText(/重新校验已无违约/)).toBeInTheDocument();
    expect(screen.getByText("确认并继续").closest("button")).toBeDisabled();

    // 重算已无违约时预填的是「请晋升」，不是「有 0 处违约待修复」——后者会让用户去改一份
    // 已经没问题的东西。
    fireEvent.click(screen.getByText("让 Agent 修复"));
    const input = useAssistantStore.getState().input;
    expect(input).toContain("validate_and_promote_draft");
    expect(input).not.toContain("违约待修复");
  });

  it("adopts externally edited (agent) content on refetch when the user has no edits", async () => {
    const edited = dramaState();
    (edited.content as { scenes: { utterances: { text: string }[] }[] }).scenes[0].utterances[1].text =
      "agent 改写后的台词";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockResolvedValueOnce(edited);

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    // 模拟 agent 在外部改了 step1 → revision 变 → 触发重新拉取
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });

    await waitFor(() => expect(screen.getByDisplayValue("agent 改写后的台词")).toBeInTheDocument());
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("preserves the user's unsaved edits when an external refetch arrives", async () => {
    const serverEdited = dramaState();
    (serverEdited.content as { scenes: { utterances: { text: string }[] }[] }).scenes[0].utterances[1].text =
      "服务端覆盖文案";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockResolvedValueOnce(serverEdited);

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    // 用户本地编辑，尚未保存
    fireEvent.change(screen.getByDisplayValue("你终于回来了。"), { target: { value: "我的本地编辑" } });
    await screen.findByText("修复后保存");

    // 外部刷新到来（agent 改 step1 → revision 变）→ 应保留用户草稿、不被服务端内容覆盖
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue("我的本地编辑")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("服务端覆盖文案")).not.toBeInTheDocument();
  });

  it("shows an empty state when there is no step1 content", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      dramaState({ status: "no_step1", content: null, fingerprint: null }),
    );
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("暂无内容整理结果")).toBeInTheDocument());
  });

  it("renders a load-error state distinct from the empty state", async () => {
    vi.spyOn(API, "getScriptReview").mockRejectedValue(new Error("网络异常"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("无法加载内容整理结果")).toBeInTheDocument());
    // 错误态展示服务端错误信息与重试入口，且不与空态文案混淆。
    expect(screen.getByText("网络异常")).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
    expect(screen.queryByText("暂无内容整理结果")).not.toBeInTheDocument();
  });

  it("surfaces an error with retry when a refetch fails after an empty state", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState({ status: "no_step1", content: null, fingerprint: null }))
      .mockRejectedValue(new Error("刷新失败"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("暂无内容整理结果")).toBeInTheDocument());

    // 空态无真实内容可保留：revision 静默刷新失败应进错误态（区别于空态）并给重试，不滞留在过时空态。
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByText("无法加载内容整理结果")).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
    expect(screen.queryByText("暂无内容整理结果")).not.toBeInTheDocument();
  });

  it("keeps existing content when a silent refetch fails", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState())
      .mockRejectedValue(new Error("刷新失败"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    // revision 触发静默刷新失败：应保留已加载内容，不闪错误态 / 空态。
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument();
    expect(screen.queryByText("无法加载内容整理结果")).not.toBeInTheDocument();
    expect(screen.queryByText("暂无内容整理结果")).not.toBeInTheDocument();
  });

  it("retries after a load error and recovers to normal content", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockRejectedValueOnce(new Error("网络异常"))
      .mockResolvedValue(dramaState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("重试")).toBeInTheDocument());

    fireEvent.click(screen.getByText("重试"));

    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());
    expect(screen.queryByText("无法加载内容整理结果")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
