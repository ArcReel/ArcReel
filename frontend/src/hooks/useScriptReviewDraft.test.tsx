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
 * One suspended `getScriptReview`: reject on abort like real fetch, exposing signals for assertions.
 * Resolve-only mocks cannot cover cancellation timing: wrongly cancelled requests would still succeed.
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
    /** Return content for this fetch; after abort, promises already rejected and this has no effect. */
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

    // Agents changed script_plan; external refresh replaces server state, but user drafts still use old baselines.
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_script_plan"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    // Retain unsaved edits after external refreshes, never overwrite with server content.
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑");

    await act(async () => {
      await result.current.save();
    });

    expect(save).toHaveBeenCalledTimes(1);
    // Submit draft-baseline fp1 for server conflict detection; refreshed fp2 would pass OCC and silently overwrite Agent edits.
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
    // Adopting save echoes aligns drafts/server state and clears dirty flags.
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

    // External-refresh GET does not resolve before user saves complete; it reads old presave content.
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

    // Adopting save echoes invalidates that GET; later old server content must never write back:
    // otherwise drafts/baselines revert to fp1 and subsequent saves hit OCC.
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
    // Agents change script_plan during saving, external refresh GET starts; clients cannot establish ordering, so always invalidate.
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

    // Refresh read DB before save persistence and now returns old content; invalidation must prevent writes.
    expect(refresh.signal?.aborted).toBe(true);
    await act(async () => {
      refresh.respond(reviewState());
      await Promise.resolve();
    });
    // Follow-up fetch after adoption compensates invalidated refreshes, preserving Agent edits.
    await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.draft?.scenes[0].utterances[0].text).toBe("agent 改写的台词"));
    save.mockResolvedValue(agentEdited);
    await act(async () => {
      await result.current.save();
    });
    expect(save.mock.calls[1][3]).toBe("fp3");
  });
});
