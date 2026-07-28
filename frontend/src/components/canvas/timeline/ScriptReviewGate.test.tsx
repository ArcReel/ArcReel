import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ScriptReviewGate } from "./ScriptReviewGate";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { ScriptReviewState } from "@/types";

function dramaState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "drama",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
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

function referenceState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    content: {
      units: [
        {
          unit_id: "E1U1",
          shots: [
            { duration: 3, text: "阿离撑伞走过长街 @[阿离]" },
            { duration: 2, text: "镜头拉近雨中石板路 @[长街]" },
          ],
          references: [
            { type: "character", name: "阿离" },
            { type: "scene", name: "长街" },
          ],
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
    expect(screen.getByText("待审核")).toBeInTheDocument();
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
      expect(screen.getByText("视觉生成已放行。再次编辑将重新进入审核。")).toBeInTheDocument(),
    );
  });

  it("edits content, surfaces save, and persists the edited intermediate", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(dramaState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(dramaState());

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByDisplayValue("你终于回来了。")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("你终于回来了。"), { target: { value: "你怎么才回来。" } });
    // 编辑后出现保存按钮
    const saveBtn = await screen.findByText("保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      scenes: [{ utterances: [{ text: "三年后。" }, { text: "你怎么才回来。" }] }],
    });
  });

  it("renders narration novel_text as editable", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(narrationState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="narration" />);

    await waitFor(() => expect(screen.getByDisplayValue("裴与出征后的第二年。")).toBeInTheDocument());
    expect(screen.getByText("E1S01")).toBeInTheDocument();
  });

  it("renders reference_video units with editable shots and read-only reference pills", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(referenceState());
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="reference_video" />);

    await waitFor(() => expect(screen.getByDisplayValue("阿离撑伞走过长街 @[阿离]")).toBeInTheDocument());
    // 第二个 shot 同样可编辑
    expect(screen.getByDisplayValue("镜头拉近雨中石板路 @[长街]")).toBeInTheDocument();
    expect(screen.getByText("E1U1")).toBeInTheDocument();
    // references 只读 pill（首现顺序 → [图N]），不进可编辑控件
    expect(screen.getByText("阿离")).toBeInTheDocument();
    expect(screen.getByText("长街")).toBeInTheDocument();
    expect(screen.getByText("待审核")).toBeInTheDocument();
  });

  it("edits a reference_video shot and persists the units draft", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(referenceState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(referenceState());

    render(<ScriptReviewGate projectName="p" episode={1} contentMode="reference_video" />);
    await waitFor(() => expect(screen.getByDisplayValue("阿离撑伞走过长街 @[阿离]")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("阿离撑伞走过长街 @[阿离]"), {
      target: { value: "阿离缓步走过长街 @[阿离]" },
    });
    const saveBtn = await screen.findByText("保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      units: [{ unit_id: "E1U1", shots: [{ text: "阿离缓步走过长街 @[阿离]" }, { text: "镜头拉近雨中石板路 @[长街]" }] }],
    });
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
    await screen.findByText("保存");

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
    await waitFor(() => expect(screen.getByText("暂无预处理内容")).toBeInTheDocument());
  });

  it("renders a load-error state distinct from the empty state", async () => {
    vi.spyOn(API, "getScriptReview").mockRejectedValue(new Error("网络异常"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);

    await waitFor(() => expect(screen.getByText("无法加载预处理内容")).toBeInTheDocument());
    // 错误态展示服务端错误信息与重试入口，且不与空态文案混淆。
    expect(screen.getByText("网络异常")).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
    expect(screen.queryByText("暂无预处理内容")).not.toBeInTheDocument();
  });

  it("surfaces an error with retry when a refetch fails after an empty state", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(dramaState({ status: "no_step1", content: null, fingerprint: null }))
      .mockRejectedValue(new Error("刷新失败"));
    render(<ScriptReviewGate projectName="p" episode={1} contentMode="drama" />);
    await waitFor(() => expect(screen.getByText("暂无预处理内容")).toBeInTheDocument());

    // 空态无真实内容可保留：revision 静默刷新失败应进错误态（区别于空态）并给重试，不滞留在过时空态。
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByText("无法加载预处理内容")).toBeInTheDocument();
    expect(screen.getByText("重试")).toBeInTheDocument();
    expect(screen.queryByText("暂无预处理内容")).not.toBeInTheDocument();
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
    expect(screen.queryByText("无法加载预处理内容")).not.toBeInTheDocument();
    expect(screen.queryByText("暂无预处理内容")).not.toBeInTheDocument();
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
    expect(screen.queryByText("无法加载预处理内容")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
