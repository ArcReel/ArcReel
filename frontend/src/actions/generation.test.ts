/**
 * 入队动作层测试：spy API 静态方法 + 真实 zustand store，
 * 验证「API 调用 → 乐观打标 → toast → 返回值归一化」的固定封装，
 * 以及 deduped=true 统一 info 提示与失败上抛不打标。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import {
  enqueueCharacter,
  enqueueEpisodeNarration,
  enqueueGrid,
  enqueueGridRegenerate,
  enqueueImageEdit,
  enqueueNarration,
  enqueueProduct,
  enqueueProp,
  enqueueReferenceVideoUnit,
  enqueueScene,
  enqueueStoryboard,
  enqueueVideo,
} from "@/actions/generation";

const SINGLE_OK = { success: true, task_id: "t1", deduped: false, message: "ok" };

function optimisticKeys(): string[] {
  return Array.from(useTasksStore.getState().optimisticActive);
}

function optimisticScriptFileKeys(): string[] {
  return Array.from(useTasksStore.getState().optimisticActiveScriptFile);
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
    expect(optimisticKeys()).toEqual(["demo\0storyboard\0seg-1\0storyboard\0"]);
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:storyboard_task_submitted_toast", { id: "seg-1" }));
    expect(toast?.tone).toBe("success");
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("deduped=true 时改弹统一 info 提示，仍打标并透出 deduped", async () => {
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({ ...SINGLE_OK, deduped: true });

    const res = await enqueueStoryboard("demo", "seg-1", "img prompt", "episode_1.json");

    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
    expect(toast?.tone).toBe("info");
    expect(optimisticKeys()).toHaveLength(1);
    expect(res.deduped).toBe(true);
  });

  it("API 失败时向上抛，不打标也不弹 toast", async () => {
    vi.spyOn(API, "generateStoryboard").mockRejectedValue(new Error("boom"));

    await expect(enqueueStoryboard("demo", "seg-1", "p", "episode_1.json")).rejects.toThrow("boom");

    expect(optimisticKeys()).toEqual([]);
    expect(useAppStore.getState().toast).toBeNull();
  });
});

describe("单资源入队动作的乐观标记 kind / taskType", () => {
  it.each([
    {
      label: "video",
      run: () => enqueueVideo("demo", "seg-1", "p", "episode_1.json", 4),
      method: "generateVideo" as const,
      key: "demo\0video\0seg-1\0video\0",
    },
    {
      label: "tts",
      run: () => enqueueNarration("demo", "seg-1", "episode_1.json"),
      method: "generateNarrationAudio" as const,
      key: "demo\0tts\0seg-1\0tts\0",
    },
    {
      label: "character",
      run: () => enqueueCharacter("demo", "Hero", "p"),
      method: "generateCharacter" as const,
      key: "demo\0character\0Hero\0character\0",
    },
    {
      label: "scene",
      run: () => enqueueScene("demo", "Temple", "p"),
      method: "generateProjectScene" as const,
      key: "demo\0scene\0Temple\0scene\0",
    },
    {
      label: "prop",
      run: () => enqueueProp("demo", "Sword", "p"),
      method: "generateProjectProp" as const,
      key: "demo\0prop\0Sword\0prop\0",
    },
    {
      label: "product",
      run: () => enqueueProduct("demo", "Phone", "p"),
      method: "generateProjectProduct" as const,
      key: "demo\0product\0Phone\0product\0",
    },
  ])("$label：成功后按资源类型打标并归一化 task_id", async ({ run, method, key }) => {
    vi.spyOn(API, method).mockResolvedValue(SINGLE_OK);

    const res = await run();

    expect(optimisticKeys()).toEqual([key]);
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
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
    expect(optimisticKeys()).toEqual([]);
    expect(optimisticScriptFileKeys()).toEqual([]);
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

    expect(optimisticKeys()).toEqual(["demo\0storyboard\0seg-1\0image_edit\0"]);
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

    expect(optimisticScriptFileKeys()).toEqual(["demo\0grid\0episode_1.json\0"]);
    expect(useAppStore.getState().toast?.text).toBe("已入队 1 个宫格");
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("task_ids 为空时不打标（无任务落库，标记会永久残留）", async () => {
    vi.spyOn(API, "generateGrid").mockResolvedValue({
      success: true,
      grid_ids: [],
      task_ids: [],
      deduped: false,
      message: "无匹配分组",
    });

    await enqueueGrid("demo", 1, "episode_1.json", ["S9"]);

    expect(optimisticScriptFileKeys()).toEqual([]);
  });
});

describe("enqueueGridRegenerate", () => {
  it("成功时静默（面板内已有状态反馈），有 scriptFile 则打标", async () => {
    vi.spyOn(API, "regenerateGrid").mockResolvedValue({ success: true, task_id: "t1", deduped: false });

    const res = await enqueueGridRegenerate("demo", "grid-1", "episode_1.json");

    expect(optimisticScriptFileKeys()).toEqual(["demo\0grid\0episode_1.json\0"]);
    expect(useAppStore.getState().toast).toBeNull();
    expect(res).toEqual({ taskIds: ["t1"], deduped: false });
  });

  it("scriptFile 为 null 时不打标；deduped=true 仍弹统一 info 提示", async () => {
    vi.spyOn(API, "regenerateGrid").mockResolvedValue({ success: true, task_id: "t1", deduped: true });

    await enqueueGridRegenerate("demo", "grid-1", null);

    expect(optimisticScriptFileKeys()).toEqual([]);
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
    expect(toast?.tone).toBe("info");
  });
});

describe("enqueueReferenceVideoUnit", () => {
  it("成功时打标并弹入队 info 提示", async () => {
    vi.spyOn(API, "generateReferenceVideoUnit").mockResolvedValue({ task_id: "t1", deduped: false });

    const res = await enqueueReferenceVideoUnit("demo", 1, "E1U1");

    expect(optimisticKeys()).toEqual(["demo\0reference_video\0E1U1\0reference_video\0"]);
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
