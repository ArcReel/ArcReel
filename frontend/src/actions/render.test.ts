import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { submitRender } from "@/actions/render";

describe("submitRender", () => {
  beforeEach(() => {
    useAppStore.setState({ toast: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("成片与剪映草稿各走自己的入队端点、剪映草稿只带旁白版本，新建任务时静默", async () => {
    const finalCut = vi
      .spyOn(API, "renderFinalCut")
      .mockResolvedValue({ task_id: "t1", deduped: false, artifact_path: "a" });
    const draft = vi
      .spyOn(API, "exportJianyingDraft")
      .mockResolvedValue({ task_id: "t2", deduped: false, artifact_path: "b" });

    const options = { narration: "with_narration", subtitles: "no_subtitles" } as const;

    expect((await submitRender("demo", "tl-1", "final_cut", options)).task_id).toBe("t1");
    expect((await submitRender("demo", "tl-1", "jianying_draft", options)).task_id).toBe("t2");

    expect(finalCut).toHaveBeenCalledWith("demo", "tl-1", options);
    expect(draft).toHaveBeenCalledWith("demo", "tl-1", { narration: "with_narration" });
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("同一产物已有任务在处理时弹统一提示，并返回已有任务", async () => {
    vi.spyOn(API, "renderFinalCut").mockResolvedValue({ task_id: "t0", deduped: true, artifact_path: "a" });

    const submission = await submitRender("demo", "tl-1", "final_cut", {
      narration: "without_narration",
      subtitles: "burned_subtitles",
    });

    expect(submission.task_id).toBe("t0");
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
    expect(toast?.tone).toBe("info");
  });
});
