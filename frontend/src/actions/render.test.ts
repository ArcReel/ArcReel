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

  it("成片与剪映草稿各走自己的入队端点，新建任务时静默", async () => {
    const finalCut = vi
      .spyOn(API, "renderFinalCut")
      .mockResolvedValue({ task_id: "t1", deduped: false, artifact_path: "a" });
    const draft = vi
      .spyOn(API, "exportJianyingDraft")
      .mockResolvedValue({ task_id: "t2", deduped: false, artifact_path: "b" });

    expect((await submitRender("demo", "tl-1", "final_cut")).task_id).toBe("t1");
    expect((await submitRender("demo", "tl-1", "jianying_draft")).task_id).toBe("t2");

    expect(finalCut).toHaveBeenCalledWith("demo", "tl-1");
    expect(draft).toHaveBeenCalledWith("demo", "tl-1");
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("同一产物已有任务在处理时弹统一提示，并返回已有任务", async () => {
    vi.spyOn(API, "renderFinalCut").mockResolvedValue({ task_id: "t0", deduped: true, artifact_path: "a" });

    const submission = await submitRender("demo", "tl-1", "final_cut");

    expect(submission.task_id).toBe("t0");
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe(i18n.t("dashboard:enqueue_deduped_toast"));
    expect(toast?.tone).toBe("info");
  });
});
