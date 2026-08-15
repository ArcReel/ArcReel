/**
 * 入队动作层测试：spy API 静态方法 + 真实 zustand store，
 * 验证「乐观打标（请求发出前）→ API 调用 → 兑现/回滚 → toast → 返回值归一化」的固定封装，
 * 以及 deduped=true 统一 info 提示与失败回滚。
 *
 * 占用一律按 selector 断言而非比对标记 key 的字面量——key 编码是 store 内部实现。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import {
  selectActiveResourceIds,
  selectHasActiveTaskForScriptFile,
  useTasksStore,
  type ResourceKind,
} from "@/stores/tasks-store";
import {
  enqueueCharacter,
  enqueueEpisodeNarration,
  enqueueGrid,
  enqueueGridRegenerate,
  enqueueImageEdit,
  enqueueNarration,
  enqueueProduct,
  enqueueProp,
  enqueueReferenceVideoBatch,
  enqueueReferenceVideoUnit,
  enqueueScene,
  enqueueStoryboard,
  enqueueVideo,
} from "@/actions/generation";

const SINGLE_OK = { success: true, task_id: "t1", deduped: false, message: "ok" };

/** 该资源是否被占用（真实任务行或乐观标记）。 */
function occupied(projectName: string, resourceKind: ResourceKind, resourceId: string): boolean {
  const { tasks, optimisticActive } = useTasksStore.getState();
  return selectActiveResourceIds(tasks, resourceKind, projectName, optimisticActive).has(resourceId);
}

/** 该剧集在指定 taskType 下是否被占用。 */
function scriptFileOccupied(projectName: string, taskType: string, scriptFile: string): boolean {
  const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
  return selectHasActiveTaskForScriptFile(
    tasks,
    taskType,
    scriptFile,
    projectName,
    optimisticActiveScriptFile,
  );
}

function markCounts(): { resource: number; scriptFile: number } {
  const s = useTasksStore.getState();
  return { resource: s.optimisticActive.size, scriptFile: s.optimisticActiveScriptFile.size };
}

beforeEach(() => {
  useTasksStore.setState({
    tasks: [],
    optimisticActive: new Set(),
    optimisticActiveScriptFile: new Set(),
  });
  useAppStore.setState({ toast: null });
});

describe("enqueueStoryboard", () => {
  it("成功时调 API、打乐观标记、弹成功 toast 并归一化返回值", async () => {
    const spy = vi.spyOn(API, "generateStoryboard").mockResolvedValue(SINGLE_OK);

    const res = await enqueueStoryboard("demo", "seg-1", "img prompt", "episode_1.json");

    expect(spy).toHaveBeenCalledWith("demo", "seg-1", "img prompt", "episode_1.json");
    expect(occupied("demo", "storyboard", "seg-1")).toBe(true);
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:storyboard_task_submitted_toast", { id: "seg-1" }));
    expect(toast?.tone).toBe("success");
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("请求发出前就完成打标，往返窗口内资源即被判为占用", async () => {
    // 打标若等到 API 返回才落，这段往返里资源判定为空闲，各调用方就得自备在途 ref
    let release: (v: typeof SINGLE_OK) => void = () => {};
    vi.spyOn(API, "generateStoryboard").mockReturnValue(
      new Promise<typeof SINGLE_OK>((resolve) => {
        release = resolve;
      }),
    );

    const pending = enqueueStoryboard("demo", "seg-1", "p", "episode_1.json");
    expect(occupied("demo", "storyboard", "seg-1")).toBe(true);

    release(SINGLE_OK);
    await pending;
    expect(occupied("demo", "storyboard", "seg-1")).toBe(true);
  });

  it("deduped=true 时改弹统一 info 提示，仍打标并透出 deduped", async () => {
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({ ...SINGLE_OK, deduped: true });

    const res = await enqueueStoryboard("demo", "seg-1", "img prompt", "episode_1.json");

    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
    expect(toast?.tone).toBe("info");
    expect(occupied("demo", "storyboard", "seg-1")).toBe(true);
    expect(res.deduped).toBe(true);
  });

  it("API 失败时向上抛并回滚乐观标记，不弹 toast", async () => {
    vi.spyOn(API, "generateStoryboard").mockRejectedValue(new Error("boom"));

    await expect(enqueueStoryboard("demo", "seg-1", "p", "episode_1.json")).rejects.toThrow("boom");

    expect(occupied("demo", "storyboard", "seg-1")).toBe(false);
    expect(markCounts().resource).toBe(0);
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("响应体形状意外时同样回滚，不留下永不清除的在途标记", async () => {
    // 在途标记不被任何轮询写回清除，故兑现前的异常路径（如 204 让 API.request 返回
    // undefined、随后取 task_id 抛 TypeError）也必须回滚，否则资源锁死到刷新为止。
    vi.spyOn(API, "generateStoryboard").mockResolvedValue(
      undefined as unknown as Awaited<ReturnType<typeof API.generateStoryboard>>,
    );

    await expect(enqueueStoryboard("demo", "seg-1", "p", "episode_1.json")).rejects.toThrow();

    expect(occupied("demo", "storyboard", "seg-1")).toBe(false);
    expect(markCounts().resource).toBe(0);
  });
});

describe("单资源入队动作的乐观标记 kind / taskType", () => {
  it("video 将请求级旁白交付与精确确认档位原样交给 API", async () => {
    const generate = vi.spyOn(API, "generateVideo").mockResolvedValue(SINGLE_OK);

    await enqueueVideo("demo", "seg-1", "p", "episode_1.json", 8, {
      narration_delivery: "use_tts",
      confirmed_request_duration_seconds: 12,
    });

    expect(generate).toHaveBeenCalledWith("demo", "seg-1", "p", "episode_1.json", 8, {
      narration_delivery: "use_tts",
      confirmed_request_duration_seconds: 12,
    });
  });

  it.each([
    {
      label: "video",
      run: () => enqueueVideo("demo", "seg-1", "p", "episode_1.json", 4),
      method: "generateVideo" as const,
      kind: "video" as const,
      resourceId: "seg-1",
    },
    {
      label: "tts",
      run: () => enqueueNarration("demo", "seg-1", "episode_1.json"),
      method: "generateNarrationAudio" as const,
      kind: "tts" as const,
      resourceId: "seg-1",
    },
    {
      label: "character",
      run: () => enqueueCharacter("demo", "Hero", "p"),
      method: "generateCharacter" as const,
      kind: "character" as const,
      resourceId: "Hero",
    },
    {
      label: "scene",
      run: () => enqueueScene("demo", "Temple", "p"),
      method: "generateProjectScene" as const,
      kind: "scene" as const,
      resourceId: "Temple",
    },
    {
      label: "prop",
      run: () => enqueueProp("demo", "Sword", "p"),
      method: "generateProjectProp" as const,
      kind: "prop" as const,
      resourceId: "Sword",
    },
    {
      label: "product",
      run: () => enqueueProduct("demo", "Phone", "p"),
      method: "generateProjectProduct" as const,
      kind: "product" as const,
      resourceId: "Phone",
    },
  ])("$label：成功后按资源类型打标并归一化 task_id", async ({ run, method, kind, resourceId }) => {
    vi.spyOn(API, method).mockResolvedValue(SINGLE_OK);

    const res = await run();

    expect(occupied("demo", kind, resourceId)).toBe(true);
    expect(markCounts().resource).toBe(1);
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it.each([
    { label: "video", run: () => enqueueVideo("demo", "seg-1", "p", "episode_1.json", 4), method: "generateVideo" as const },
    { label: "character", run: () => enqueueCharacter("demo", "Hero", "p"), method: "generateCharacter" as const },
  ])("$label：请求失败时回滚，不留下占用", async ({ run, method }) => {
    vi.spyOn(API, method).mockRejectedValue(new Error("boom"));

    await expect(run()).rejects.toThrow("boom");

    expect(markCounts()).toEqual({ resource: 0, scriptFile: 0 });
  });
});

describe("enqueueEpisodeNarration", () => {
  it("有缺失片段时弹批量提交 toast，不打乐观标记", async () => {
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: ["t1", "t2"],
      deduped: false,
      message: "ok",
    });

    const res = await enqueueEpisodeNarration("demo", "episode_1.json");

    expect(useAppStore.getState().toast?.text).toBe(
      i18n.t("dashboard:narration_batch_submitted_toast", { count: 2 }),
    );
    expect(markCounts()).toEqual({ resource: 0, scriptFile: 0 });
    expect(res).toEqual({ taskIds: ["t1", "t2"], deduped: false });
  });

  it("无缺失片段（task_ids 为空）时弹无缺失提示", async () => {
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: [],
      deduped: false,
      message: "ok",
    });

    await enqueueEpisodeNarration("demo", "episode_1.json");

    expect(useAppStore.getState().toast?.text).toBe(
      i18n.t("dashboard:narration_batch_none_missing_toast"),
    );
  });
});

describe("enqueueImageEdit", () => {
  it("按被编辑资源类型归槽打标，taskType 固定 image_edit，toast 用后端 message", async () => {
    vi.spyOn(API, "editImage").mockResolvedValue({ ...SINGLE_OK, message: "已提交图片编辑" });

    const res = await enqueueImageEdit("demo", {
      resourceType: "storyboard",
      resourceId: "seg-1",
      instruction: "去掉水印",
      scriptFile: "episode_1.json",
    });

    // 编辑任务与目标资源的生成任务同槽：按 storyboard 归槽而非 image_edit
    expect(occupied("demo", "storyboard", "seg-1")).toBe(true);
    expect(useAppStore.getState().toast?.text).toBe("已提交图片编辑");
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });
});

describe("enqueueGrid", () => {
  it("task_ids 非空时按 scriptFile 粒度打标，toast 用后端 message", async () => {
    vi.spyOn(API, "generateGrid").mockResolvedValue({
      success: true,
      grid_ids: ["g1"],
      task_ids: ["t1"],
      deduped: false,
      message: "已入队 1 个宫格",
    });

    const res = await enqueueGrid("demo", 1, "episode_1.json");

    expect(scriptFileOccupied("demo", "grid", "episode_1.json")).toBe(true);
    expect(useAppStore.getState().toast?.text).toBe("已入队 1 个宫格");
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("task_ids 为空时回滚标记（无任务落库，标记会永久残留）", async () => {
    vi.spyOn(API, "generateGrid").mockResolvedValue({
      success: true,
      grid_ids: [],
      task_ids: [],
      deduped: false,
      message: "无匹配分组",
    });

    await enqueueGrid("demo", 1, "episode_1.json", ["S9"]);

    expect(markCounts().scriptFile).toBe(0);
    expect(scriptFileOccupied("demo", "grid", "episode_1.json")).toBe(false);
  });
});

describe("enqueueGridRegenerate", () => {
  it("成功时静默（面板内已有状态反馈），宫格与所属剧集同时打标", async () => {
    vi.spyOn(API, "regenerateGrid").mockResolvedValue({ success: true, task_id: "t1", deduped: false });

    const res = await enqueueGridRegenerate("demo", "grid-1", "episode_1.json");

    expect(occupied("demo", "grid", "grid-1")).toBe(true);
    expect(scriptFileOccupied("demo", "grid", "episode_1.json")).toBe(true);
    expect(useAppStore.getState().toast).toBeNull();
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("scriptFile 为 null 时只打宫格粒度标记；deduped=true 仍弹统一 info 提示", async () => {
    vi.spyOn(API, "regenerateGrid").mockResolvedValue({ success: true, task_id: "t1", deduped: true });

    await enqueueGridRegenerate("demo", "grid-1", null);

    expect(markCounts()).toEqual({ resource: 1, scriptFile: 0 });
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
    expect(toast?.tone).toBe("info");
  });

  it("请求失败时两个粒度的标记一起回滚", async () => {
    vi.spyOn(API, "regenerateGrid").mockRejectedValue(new Error("boom"));

    await expect(enqueueGridRegenerate("demo", "grid-1", "episode_1.json")).rejects.toThrow("boom");

    expect(markCounts()).toEqual({ resource: 0, scriptFile: 0 });
  });
});

describe("enqueueReferenceVideoUnit", () => {
  it("成功时打标并弹入队 info 提示", async () => {
    const generate = vi
      .spyOn(API, "generateReferenceVideoUnit")
      .mockResolvedValue({ task_id: "t1", deduped: false });

    const res = await enqueueReferenceVideoUnit("demo", 1, "E1U1", {
      confirmed_request_duration_seconds: 8,
    });

    expect(generate).toHaveBeenCalledWith("demo", 1, "E1U1", {
      confirmed_request_duration_seconds: 8,
    });
    expect(occupied("demo", "reference_video", "E1U1")).toBe(true);
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:reference_generate_queued"));
    expect(toast?.tone).toBe("info");
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("deduped=true 时改弹统一去重提示", async () => {
    vi.spyOn(API, "generateReferenceVideoUnit").mockResolvedValue({ task_id: "t1", deduped: true });

    await enqueueReferenceVideoUnit("demo", 1, "E1U1");

    expect(useAppStore.getState().toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
  });
});

describe("enqueueReferenceVideoBatch", () => {
  const ADMISSION = {
    operation: "generate_reference_videos_batch",
    selection: "explicit",
    narration_delivery: "post_production",
    units: [],
    confirmation: null,
    skipped_unit_ids: [],
    deduped: false,
  };

  it("admitted 时按目标单元打标并弹入队提示", async () => {
    const batch = vi
      .spyOn(API, "generateReferenceVideoBatch")
      .mockResolvedValue({ ...ADMISSION, decision: "admitted", task_ids: ["t1", "t2"] } as never);

    const res = await enqueueReferenceVideoBatch("demo", 1, { unit_ids: ["E1U1", "E1U2"] });

    expect(batch).toHaveBeenCalledWith("demo", 1, { unit_ids: ["E1U1", "E1U2"] });
    expect(occupied("demo", "reference_video", "E1U1")).toBe(true);
    expect(occupied("demo", "reference_video", "E1U2")).toBe(true);
    expect(useAppStore.getState().toast?.text).toBe(
      i18n.t("dashboard:reference_batch_queued", { count: 2 }),
    );
    expect(res.decision).toBe("admitted");
  });

  // confirmation_required / blocked 一个任务也没建：占用标记必须整批回滚，
  // 否则界面会把没入队的单元显示成生成中，直到刷新页面。
  it.each(["confirmation_required", "blocked"] as const)(
    "%s 时回滚占用标记且不弹入队提示",
    async (decision) => {
      vi.spyOn(API, "generateReferenceVideoBatch").mockResolvedValue({
        ...ADMISSION,
        decision,
        task_ids: [],
      } as never);

      const res = await enqueueReferenceVideoBatch("demo", 1, { unit_ids: ["E1U1"] });

      expect(res.decision).toBe(decision);
      expect(occupied("demo", "reference_video", "E1U1")).toBe(false);
      expect(useAppStore.getState().toast).toBeNull();
    },
  );

  it("请求失败时回滚占用标记并原样抛出", async () => {
    vi.spyOn(API, "generateReferenceVideoBatch").mockRejectedValue(new Error("boom"));

    await expect(enqueueReferenceVideoBatch("demo", 1, { unit_ids: ["E1U1"] })).rejects.toThrow(
      "boom",
    );

    expect(markCounts()).toEqual({ resource: 0, scriptFile: 0 });
  });
});
