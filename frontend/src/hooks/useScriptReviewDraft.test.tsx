import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API, ApiRequestError } from "@/api";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import type { DramaNormalizedScript, ScriptReviewState } from "@/types";

function reviewState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "drama",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    blocking_issues: [],
    soft_violations: [],
    supported_durations: null,
    duration_tiers: null,
    episode_target_duration: null,
    script_overwrite: null,
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
          utterances: [{ kind: "dialogue", speaker: "阿离", text: "你终于回来了。" }],
          source_text: "阿离：你终于回来了。",
        },
      ],
    },
    ...overrides,
  };
}

function selectContent(state: ScriptReviewState): DramaNormalizedScript | null {
  return (state.content ?? null) as DramaNormalizedScript | null;
}

const noop = () => {};

/**
 * 一次挂起的 `getScriptReview`：像真实 fetch 一样在 abort 时 reject，并留出 signal 供断言。
 * 只用 resolve 的 mock 无法覆盖取消时序——请求被误取消时它仍会成功返回。
 */
function deferredGet() {
  let captured: AbortSignal | null = null;
  let settle: (state: ScriptReviewState) => void = noop;
  const impl = (_projectName: string, _episode: number, options?: { signal?: AbortSignal }) => {
    captured = options?.signal ?? null;
    return new Promise<ScriptReviewState>((resolve, reject) => {
      settle = resolve;
      options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
  };
  return {
    impl,
    respond: (state: ScriptReviewState) => settle(state),
    get signal() {
      return captured;
    },
  };
}

function renderDraft() {
  return renderHook(() =>
    useScriptReviewDraft<DramaNormalizedScript>({
      projectName: "p",
      episode: 1,
      selectContent,
      onConfirmed: noop,
    }),
  );
}

function editFirstUtterance(draft: DramaNormalizedScript, text: string): DramaNormalizedScript {
  return {
    ...draft,
    scenes: draft.scenes.map((s, i) =>
      i === 0 ? { ...s, utterances: s.utterances.map((u, j) => (j === 0 ? { ...u, text } : u)) } : s,
    ),
  };
}

describe("useScriptReviewDraft", () => {
  afterEach(() => vi.restoreAllMocks());

  it("saves with the fingerprint of the content the draft was based on, not the refreshed one", async () => {
    const agentEdited = reviewState({ fingerprint: "fp2" });
    (agentEdited.content as DramaNormalizedScript).scenes[0].utterances[0].text = "agent 改写的台词";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState())
      .mockResolvedValueOnce(agentEdited);
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(reviewState());

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "我的本地编辑") : prev));
    });
    expect(result.current.dirty).toBe(true);

    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑");

    await act(async () => {
      await result.current.save();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][3]).toBe("fp1");
  });

  it("adopts the saved content as the new baseline, so a follow-up save carries the new fingerprint", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(reviewState());
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(reviewState({ fingerprint: "fp2" }));

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "第一次编辑") : prev));
    });
    await act(async () => {
      await result.current.save();
    });
    expect(result.current.dirty).toBe(false);

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "第二次编辑") : prev));
    });
    await act(async () => {
      await result.current.save();
    });

    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][3]).toBe("fp2");
  });

  it("cancels the pull that was in flight when the save is adopted, so it cannot roll the draft back", async () => {
    const pull = deferredGet();
    const saved = reviewState({ fingerprint: "fp2" });
    (saved.content as DramaNormalizedScript).scenes[0].utterances[0].text = "我的本地编辑";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState())
      .mockImplementationOnce(pull.impl)
      .mockResolvedValue(saved);
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(saved);

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());

    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "我的本地编辑") : prev));
    });
    await act(async () => {
      await result.current.save();
    });

    expect(pull.signal?.aborted).toBe(true);
    await act(async () => {
      pull.respond(reviewState());
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑"));
    await act(async () => {
      await result.current.save();
    });
    expect(save.mock.calls[1][3]).toBe("fp2");
  });

  it("re-pulls after adopting, so an external refresh cancelled by the save is not lost", async () => {
    const agentEdited = reviewState({ fingerprint: "fp3" });
    (agentEdited.content as DramaNormalizedScript).scenes[0].utterances[0].text = "agent 改写的台词";
    const refresh = deferredGet();
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState())
      .mockImplementationOnce(refresh.impl)
      .mockResolvedValue(agentEdited);
    let resolveSave: (state: ScriptReviewState) => void = noop;
    const savePromise = new Promise<ScriptReviewState>((resolve) => {
      resolveSave = resolve;
    });
    const save = vi.spyOn(API, "saveScriptReviewContent").mockReturnValueOnce(savePromise);

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "我的本地编辑") : prev));
    });
    let saving: Promise<boolean>;
    act(() => {
      saving = result.current.save();
    });
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveSave(reviewState({ fingerprint: "fp2" }));
      await saving;
    });

    expect(refresh.signal?.aborted).toBe(true);
    await act(async () => {
      refresh.respond(reviewState());
      await Promise.resolve();
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.draft?.scenes[0].utterances[0].text).toBe("agent 改写的台词"));
    save.mockResolvedValue(agentEdited);
    await act(async () => {
      await result.current.save();
    });
    expect(save.mock.calls[1][3]).toBe("fp3");
  });

  it("refreshes on script_plan invalidation, marks outdated when dirty, and preserves unsaved edits on 409 conflict", async () => {
    const agentEdited = reviewState({ fingerprint: "fp2" });
    (agentEdited.content as DramaNormalizedScript).scenes[0].utterances[0].text = "agent 增量修改后的台词";
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState())
      .mockResolvedValueOnce(agentEdited)
      .mockResolvedValue(agentEdited);
    vi.spyOn(API, "saveScriptReviewContent").mockRejectedValueOnce(
      new ApiRequestError("脚本规划在编辑期间已被其他编辑方修改", { code: "revision_conflict" }, 409),
    );

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());
    expect(result.current.outdated).toBe(false);

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "我的本地编辑") : prev));
    });

    await act(async () => {
      await result.current.save();
    });

    expect(result.current.outdated).toBe(true);
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑");
    expect(useAppStore.getState().toast?.tone).toBe("warning");
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑");

    act(() => {
      result.current.reloadLatest();
    });

    await waitFor(() => expect(result.current.draft?.scenes[0].utterances[0].text).toBe("agent 增量修改后的台词"));
    expect(result.current.outdated).toBe(false);
    expect(result.current.dirty).toBe(false);
  });

  it("adopts remaining blocking issues on save and skips confirm if dirty save still has blocking issues", async () => {
    const issue = { code: "source_text_empty", label: "scene E1S01", message: "原文为空", line: null, item_index: 0 };
    vi.spyOn(API, "getScriptReview").mockResolvedValue(reviewState());
    vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(
      reviewState({ fingerprint: "fp2", blocking_issues: [issue] }),
    );
    const confirm = vi.spyOn(API, "confirmScriptReview");

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "改了一半") : prev));
    });

    let ok = true;
    await act(async () => {
      ok = await result.current.confirm();
    });

    expect(ok).toBe(false);
    expect(confirm).not.toHaveBeenCalled();
    expect(result.current.state?.blocking_issues).toEqual([issue]);
    expect(useAppStore.getState().toast?.text).toContain("1 项确认阻断项");
  });

  it("re-fetches review state when video model capabilities are invalidated", async () => {
    const capabilityIssue = {
      code: "video_capability_unavailable",
      label: "",
      message: "视频模型能力不可用",
      line: null,
      item_index: null,
    };
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState({ blocking_issues: [capabilityIssue] }))
      .mockResolvedValueOnce(reviewState({ blocking_issues: [] }));

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.state?.blocking_issues).toEqual([capabilityIssue]));

    act(() => {
      useCapabilitiesStore.getState().invalidate();
    });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.state?.blocking_issues).toEqual([]));
  });
});
