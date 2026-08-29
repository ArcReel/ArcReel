import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { useAppStore } from "@/stores/app-store";
import type { DramaNormalizedScript, ScriptReviewState } from "@/types";

function reviewState(overrides: Partial<ScriptReviewState> = {}): ScriptReviewState {
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
    /** 让这次拉取返回内容；已被 abort 时 promise 已 reject，此处即无效果。 */
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

    // agent 改了 script_plan → 外部刷新把服务端态换成新版，但用户草稿仍基于旧版
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    // 未保存编辑在外部刷新后保留，不被服务端内容覆盖
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑");

    await act(async () => {
      await result.current.save();
    });

    expect(save).toHaveBeenCalledTimes(1);
    // 提交草稿所基于的 fp1，服务端才能判出冲突；提交刷新后的 fp2 会让 OCC 放行、静默覆盖 agent 的修改
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
    // 采纳保存回显后草稿与服务端一致，脏标记复位
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

    // 外部刷新发出 GET，它在用户保存完成前都不 resolve——读到的是保存前的旧内容
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

    // 采纳保存回显时该 GET 被作废；此刻服务端才回旧内容，作废后它不再回写：
    // 否则草稿回退、基线指纹退回 fp1，下次保存撞 OCC
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
    // 保存在途时 agent 改了 script_plan，外部刷新发出 GET；它与保存的先后无法在客户端判定，一律作废
    let saving: Promise<void>;
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

    // 该刷新读库早于保存落库，此刻返回的是保存前的旧内容——作废后不得回写
    expect(refresh.signal?.aborted).toBe(true);
    await act(async () => {
      refresh.respond(reviewState());
      await Promise.resolve();
    });
    // 作废的那次刷新由采纳后补发的一轮拉取补回，agent 的修改不丢
    await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.draft?.scenes[0].utterances[0].text).toBe("agent 改写的台词"));
    save.mockResolvedValue(agentEdited);
    await act(async () => {
      await result.current.save();
    });
    expect(save.mock.calls[1][3]).toBe("fp3");
  });
});
