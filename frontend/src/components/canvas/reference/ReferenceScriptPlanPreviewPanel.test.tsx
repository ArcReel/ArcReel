import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { ReferenceScriptPlanPreviewPanel } from "./ReferenceScriptPlanPreviewPanel";
import type { MentionLookup } from "@/hooks/useUnitPromptHighlight";
import type { ReferenceScriptPlanDraft, ScriptReviewState } from "@/types";

const LOOKUP: MentionLookup = { 阿离: "character", 长街: "scene" };

function pendingState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: [4, 8],
    duration_tiers: null,
    content: {
      units: [
        {
          unit_id: "E1U01",
          text: "@[阿离] 撑伞走过 @[长街]",
          duration_seconds: 8,
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
    supported_durations: [4, 8],
    duration_tiers: null,
    content: null,
    quarantine: {
      content: {
        units: [
          {
            duration_seconds: 8,
            source_text: "阿离撑伞走过长街。",
            text: "门开了\n@[阿离]：｛我来了。｝",
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

describe("ReferenceScriptPlanPreviewPanel", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    // 确认后的全局副作用（toast + 预填）只在「用户仍在看这个项目」时才生效，测试渲染面板时
    // 用的 projectName="p"，需要同步告诉 store 当前正在看的就是它。
    useProjectsStore.setState({ currentProjectName: "p" });
  });
  afterEach(() => vi.restoreAllMocks());

  // `@[名称]` 在正文里高亮；参考图仅在执行期解析，无独立参考图清单。
  it("renders the clean pending state with the highlighted body and no reference list", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());
    expect(screen.getByText("阿离撑伞走过长街。")).toBeInTheDocument();
    expect(screen.getByText("@[阿离]")).toBeInTheDocument();
    expect(screen.getByText("@[长街]")).toBeInTheDocument();
    expect(screen.queryByText("参考图")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeEnabled();
  });

  it("localizes structured speech violations with their unit and field locations", async () => {
    const state = quarantinedState();
    state.quarantine!.violations = [{
      code: "mixed_speech",
      label: "unit E1U01",
      message: "raw agent message",
      line: null,
      locations: [
        { path: ["text"], line: 1 },
        { path: ["text"], line: 2 },
      ],
      reason: "character_and_narrator_mixed",
      action: "replan_unit",
    }];
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText(/视频单元 E1U01.*同时包含角色台词和旁白/)).toHaveTextContent("text:2");
    expect(screen.queryByText("raw agent message")).not.toBeInTheDocument();
  });

  // 场景引用提示：与解析预览面板同判据，落在 unit 卡内，不阻断确认。
  it("warns on units that reference no scene while the project registers scene assets", async () => {
    const state = pendingState();
    state.content = { units: [{ unit_id: "E1U01", text: "@[阿离] 推门。", duration_seconds: 8, source_text: "阿离推门。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("本单元未引用场景，画面地点将由模型自由决定")).toBeInTheDocument();
    // 降级提示不进确认闸门：正文合法，只是画面地点没被钉住。
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeEnabled();
  });

  it("drops the scene warning once the edited body references a scene", async () => {
    const state = pendingState();
    state.content = { units: [{ unit_id: "E1U01", text: "@[阿离] 推门。", duration_seconds: 8, source_text: "阿离推门。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "E1U01 正文" }), {
      target: { value: "@[阿离] 推门走进 @[长街]。" },
    });

    await waitFor(() =>
      expect(screen.queryByText("本单元未引用场景，画面地点将由模型自由决定")).not.toBeInTheDocument(),
    );
  });

  it("stays silent about scenes when the project registers none", async () => {
    const state = pendingState();
    state.content = { units: [{ unit_id: "E1U01", text: "@[阿离] 推门。", duration_seconds: 8, source_text: "阿离推门。" }] };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={{ 阿离: "character" }} />);

    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());
    expect(screen.queryByText("本单元未引用场景，画面地点将由模型自由决定")).not.toBeInTheDocument();
  });

  it("confirms, then prefills a continue message into the assistant input without sending", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockResolvedValue(pendingState({ status: "confirmed", quarantine: null }));

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /确认拆分，继续生成/ }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("p", 1));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("第 1 集"));
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("suppresses the confirm toast/prefill if the user has switched to a different project mid-request", async () => {
    useAppStore.setState({ assistantPanelOpen: false });
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    let resolveConfirm: (value: ScriptReviewState) => void = () => {};
    vi.spyOn(API, "confirmScriptReview").mockReturnValue(
      new Promise((resolve) => {
        resolveConfirm = resolve;
      }),
    );

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: /确认拆分，继续生成/ }));

    // 确认请求在途时用户切到了另一个项目（本组件所在的 tab 可能因此被卸载，但即使还挂载着
    // 也不该再写全局副作用）。
    useProjectsStore.setState({ currentProjectName: "other-project" });
    resolveConfirm(pendingState({ status: "confirmed", quarantine: null }));

    // adopt() 运行在守卫之前，确认本身已生效——用按钮态的变化确认异步流程真的跑完了，
    // 而不是靠一个从始至终都为空的断言碰巧「通过」。
    await waitFor(() => expect(screen.getByRole("button", { name: "已确认" })).toBeDisabled());
    expect(useAssistantStore.getState().input).toBe("");
    expect(useAppStore.getState().toast).toBeNull();
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
  });

  it("still shows the confirm toast/prefill after switching tabs within the same project", async () => {
    // 同项目内切 tab（本组件会被卸载，但 useProjectsStore.currentProjectName 不变）不该被
    // 当成「切走了」而抑制全局副作用——预填文案本身带着具体集号，写进全局输入框依然准确。
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    let resolveConfirm: (value: ScriptReviewState) => void = () => {};
    vi.spyOn(API, "confirmScriptReview").mockReturnValue(
      new Promise((resolve) => {
        resolveConfirm = resolve;
      }),
    );

    const { unmount } = render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: /确认拆分，继续生成/ }));

    unmount();
    resolveConfirm(pendingState({ status: "confirmed", quarantine: null }));

    await waitFor(() => expect(useAssistantStore.getState().input).toContain("第 1 集"));
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("quarantined state anchors a line-level violation inline and aggregates the unit-level one, blocking confirm", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());
    expect(screen.getByText("unit E1U01 使用了全角花括号")).toBeInTheDocument();
    expect(screen.getByText("unit E1U01 的台词念不完")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "让 Agent 修复" })).toBeInTheDocument();
  });

  it("prefills a structured fix-request report on 'ask the assistant to fix it', without sending", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "让 Agent 修复" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "让 Agent 修复" }));

    const input = useAssistantStore.getState().input;
    expect(input).toContain("第 1 集");
    expect(input).toContain("doc_type=reference_script_plan");
    expect(input).toContain("open_draft 返回的 revision 作为 base_revision");
    expect(input).toContain("1. unit E1U01 使用了全角花括号");
    expect(input).toContain("2. unit E1U01 的台词念不完");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("shows an empty state when there is no script_plan content", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      episode: 1,
      content_mode: "narration",
      status: "no_script_plan",
      fingerprint: null,
      confirmed_at: null,
      content: null,
      quarantine: null,
      supported_durations: null,
      duration_tiers: null,
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByText("暂无脚本规划结果")).toBeInTheDocument());
  });

  it("edits the unit body in the non-quarantined state and persists the units draft", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(pendingState());

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await waitFor(() => expect(screen.getByText("E1U01")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("@[阿离] 撑伞走过 @[长街]");
    fireEvent.change(textarea, { target: { value: "@[阿离] 缓步走过 @[长街]" } });

    const saveBtn = await screen.findByText("保存");
    fireEvent.click(saveBtn);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [, , savedContent, baseFingerprint] = save.mock.calls[0];
    expect(savedContent).toMatchObject({
      units: [{ unit_id: "E1U01", text: "@[阿离] 缓步走过 @[长街]" }],
    });
    // 保存携带 GET 时拿到的内容指纹，供服务端做并发编辑冲突比对
    expect(baseFingerprint).toBe("fp1");
  });

  it("renders the spoken-line count in the unit header", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(quarantinedState());
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // 草稿正文一行描述 + 一行全角花括号「台词」：统计与正文高亮同一套切分口径，全角花括号
    // 不被解析器认作台词（这正是该草稿的 fullwidth_braces 违约），故台词数为 0。
    await waitFor(() => expect(screen.getByText("0 句台词")).toBeInTheDocument());
  });

  it("counts a well-formed dialogue line as a spoken line", async () => {
    const withDialogue = pendingState();
    (withDialogue.content as ReferenceScriptPlanDraft).units[0].text =
      "@[阿离] 撑伞走过 @[长街]\n@[阿离]：{我来了。}";
    vi.spyOn(API, "getScriptReview").mockResolvedValue(withDialogue);
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("1 句台词")).toBeInTheDocument());
  });

  it("picks a duration from the supported tiers and saves it on the unit", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(pendingState());

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    const select = await screen.findByRole("combobox", { name: "E1U01 时长" });
    fireEvent.change(select, { target: { value: "4" } });

    fireEvent.click(await screen.findByText("保存"));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][2]).toMatchObject({ units: [{ duration_seconds: 4 }] });
  });

  it("falls back to a read-only duration when no tier list is available", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ supported_durations: null }));
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("8 秒")).toBeInTheDocument());
    expect(screen.queryByRole("combobox", { name: "E1U01 时长" })).not.toBeInTheDocument();
  });

  it("keeps the duration select on a stored value that is no longer a supported tier, sorted into place", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState({ supported_durations: [4, 6] }));
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });
    expect(select.value).toBe("8");
    // 越档兜底项插入 options 时按数值排序，不是简单地把当前值塞到最前面（那样 8/4/6 的
    // 显示顺序会乱）。
    expect([...select.options].map((o) => o.value)).toEqual(["4", "6", "8"]);
  });

  it("surfaces unit-less violations and the raw draft when the quarantined content has no usable units", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...quarantinedState(),
      quarantine: {
        // schema 违约：后端原样回传 Agent 手改的内容，`units` 根本不是数组。
        content: { units: "被改坏了" } as never,
        violations: [{ code: "schema_invalid", label: "", message: "待修复草稿的 content.units 必须是非空数组", line: null }],
      },
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    await waitFor(() => expect(screen.getByText("无法锚定的违约")).toBeInTheDocument());
    expect(screen.getByText("待修复草稿的 content.units 必须是非空数组")).toBeInTheDocument();
    expect(screen.getByText(/被改坏了/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
  });

  it("disables the duration select and body textarea while a save is in flight", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(pendingState());
    let resolveSave: (value: ScriptReviewState) => void = () => {};
    vi.spyOn(API, "saveScriptReviewContent").mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );

    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("@[阿离] 撑伞走过 @[长街]");
    fireEvent.change(textarea, { target: { value: "@[阿离] 缓步走过 @[长街]" } });
    const select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });

    fireEvent.click(await screen.findByText("保存"));

    await waitFor(() => expect(textarea).toBeDisabled());
    expect(select).toBeDisabled();

    resolveSave(pendingState());
    await waitFor(() => expect(textarea).toBeEnabled());
  });

  it("picks the with-references duration tier for a unit that carries references", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({
        supported_durations: [4, 6, 8],
        duration_tiers: { with_references: [8], without_references: [4, 6, 8] },
      }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // unit 带 @[阿离]/@[长街] 引用：按 with_references 档位收窄，4/6 秒不再可选。
    const select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });
    expect([...select.options].map((o) => o.value)).toEqual(["8"]);
  });

  it("counts a merchandise-only body as carrying references", async () => {
    const state = pendingState({
      supported_durations: [4, 6, 8],
      duration_tiers: { with_references: [8], without_references: [4, 6, 8] },
    });
    (state.content as ReferenceScriptPlanDraft).units[0].text = "@[保温杯] 特写";
    (state.content as ReferenceScriptPlanDraft).units[0].duration_seconds = 8;
    vi.spyOn(API, "getScriptReview").mockResolvedValue(state);
    render(
      <ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={{ ...LOOKUP, 保温杯: "product" }} />,
    );

    // 商品与其它资产同规则派生参考图，档位按 with_references 收窄。
    const select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });
    expect([...select.options].map((o) => o.value)).toEqual(["8"]);
  });

  it("blocks confirmation and flags a unit whose stored duration has fallen out of the effective tier", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(
      pendingState({
        supported_durations: [4, 6, 8],
        // unit 带引用，生效档位收窄到 4/6 秒——已存盘的 8 秒不再合法，但仍要照旧展示（不静默跳档）。
        duration_tiers: { with_references: [4, 6], without_references: [4, 6, 8] },
      }),
    );
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    const select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });
    expect(select.value).toBe("8");
    expect(screen.getByText("档位已失效")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
  });

  it("recomputes the tier choice from the live-edited body", async () => {
    const withoutReferences = pendingState({
      supported_durations: [4, 6, 8],
      // 8 秒是两套档位共有的值，保证初始展示不触发「越档补首值」分支，让第二次断言干净地
      // 反映档位切换本身，而不是与该兜底行为的展示叠在一起。
      duration_tiers: { with_references: [8], without_references: [4, 6, 8] },
    });
    (withoutReferences.content as ReferenceScriptPlanDraft).units[0].text = "门开了";
    (withoutReferences.content as ReferenceScriptPlanDraft).units[0].duration_seconds = 8;
    vi.spyOn(API, "getScriptReview").mockResolvedValue(withoutReferences);
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    // 初始无引用：按 without_references 档位，4/6/8 全可选。
    let select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });
    expect([...select.options].map((o) => o.value).sort()).toEqual(["4", "6", "8"]);

    // 编辑正文新增 @[阿离] 引用（尚未保存）：档位应立即按 with_references 收窄到仅 8 秒。
    fireEvent.click(screen.getByRole("button", { name: "编辑文稿" }));
    const textarea = await screen.findByDisplayValue("门开了");
    fireEvent.change(textarea, { target: { value: "@[ 阿离 ] 推门而入。" } });

    select = await screen.findByRole<HTMLSelectElement>("combobox", { name: "E1U01 时长" });
    expect([...select.options].map((o) => o.value)).toEqual(["8"]);
  });

  it("offers promotion when the draft has no violations", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...quarantinedState(),
      quarantine: { content: quarantinedState().quarantine!.content, violations: [] },
    });
    render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);

    expect(await screen.findByText("草稿由 Agent 处理")).toBeInTheDocument();
    expect(screen.queryByText("待修复草稿 — 拆分未通过校验")).not.toBeInTheDocument();
    expect(screen.getByText("Agent 会在本集任务中继续处理草稿，完成后此处会自动更新")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "让 Agent 修复" }));
    const input = useAssistantStore.getState().input;
    expect(input).toContain("open_draft");
    expect(input).toContain("promote_draft");
    expect(input).toContain("doc_type=reference_script_plan");
    expect(input).toContain("revision");
    expect(input).toContain("base_revision");
    expect(input).not.toContain("违约待修复");
    // 禁用判据是待处置草稿文件是否在场，不是重算后的违约数量——违约为空但草稿仍在场时确认依旧禁用。
    expect(screen.getByRole("button", { name: /确认拆分，继续生成/ })).toBeDisabled();
  });

  it("separates multiple violating-unit locator links in the status bar", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue({
      ...quarantinedState(),
      quarantine: {
        content: {
          units: [
            { duration_seconds: 8, source_text: "阿离撑伞走过长街。", text: "门开了\n@[阿离]：｛我来了。｝" },
            { duration_seconds: 4, source_text: "长街空无一人。", text: "门开了\n@[长街]：｛静悄悄。｝" },
          ],
        },
        violations: [
          { code: "fullwidth_braces", label: "unit E1U01", message: "unit E1U01 使用了全角花括号", line: 1 },
          { code: "fullwidth_braces", label: "unit E1U02", message: "unit E1U02 使用了全角花括号", line: 1 },
        ],
      },
    });
    const { container } = render(<ReferenceScriptPlanPreviewPanel projectName="p" episode={1} lookup={LOOKUP} />);
    await screen.findByRole("button", { name: "E1U01 · 1" });

    // 两个按钮之间的可见文本要有分隔符，不能粘连成 "E1U01 · 1E1U02 · 1"——按钮各自的可访问名
    // 本身不受这个 bug 影响（那是每个元素独立算的），只有渲染出的原始文本会粘连，所以这里
    // 直接断言状态条的 textContent。
    const statusBar = container.querySelector("span.text-\\[11px\\].text-text-4");
    expect(statusBar?.textContent).toMatch(/E1U01 · 1.+E1U02 · 1/);
    expect(statusBar?.textContent).not.toContain("1E1U02");
  });
});
