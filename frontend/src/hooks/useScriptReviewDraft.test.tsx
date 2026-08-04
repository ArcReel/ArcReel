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

    // agent 改了 step1 → 外部刷新把服务端态换成新版，但用户草稿仍基于旧版
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
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

  it("keeps the adopted content when a pull issued before the save resolves afterwards", async () => {
    let resolvePending: (state: ScriptReviewState) => void = noop;
    const pending = new Promise<ScriptReviewState>((resolve) => {
      resolvePending = resolve;
    });
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState())
      .mockReturnValueOnce(pending);
    const saved = reviewState({ fingerprint: "fp2" });
    (saved.content as DramaNormalizedScript).scenes[0].utterances[0].text = "我的本地编辑";
    const save = vi.spyOn(API, "saveScriptReviewContent").mockResolvedValue(saved);

    const { result } = renderDraft();
    await waitFor(() => expect(result.current.draft).not.toBeNull());

    // 外部刷新发出 GET，但它在用户保存完成前都不 resolve
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));

    act(() => {
      result.current.setDraft((prev) => (prev ? editFirstUtterance(prev, "我的本地编辑") : prev));
    });
    await act(async () => {
      await result.current.save();
    });

    // 保存前发出的 GET 此刻才回来，携带保存前的旧内容与旧指纹
    await act(async () => {
      resolvePending(reviewState());
      await pending;
    });

    // 旧响应不得回写：草稿保持刚采纳的内容，基线指纹保持 fp2（否则下次保存拿 fp1 撞 OCC）
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("我的本地编辑");
    await act(async () => {
      await result.current.save();
    });
    expect(save.mock.calls[1][3]).toBe("fp2");
  });

  it("keeps a refresh that started after the save was issued", async () => {
    const agentEdited = reviewState({ fingerprint: "fp3" });
    (agentEdited.content as DramaNormalizedScript).scenes[0].utterances[0].text = "agent 改写的台词";
    let resolveRefresh: (state: ScriptReviewState) => void = noop;
    const refresh = new Promise<ScriptReviewState>((resolve) => {
      resolveRefresh = resolve;
    });
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState())
      .mockReturnValueOnce(refresh);
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
    // 保存发出后仍在途时，agent 改了 step1 触发外部刷新——这个 GET 晚于保存，携带更新的服务端态
    let saving: Promise<void>;
    act(() => {
      saving = result.current.save();
    });
    act(() => {
      useAppStore.getState().invalidateEntities(["draft:episode_1_step1"]);
    });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveSave(reviewState({ fingerprint: "fp2" }));
      await saving;
    });
    await act(async () => {
      resolveRefresh(agentEdited);
      await refresh;
    });

    // 晚于保存发出的刷新不得被误杀：触发它的 revision 已被消费，作废后不会再有请求补上
    expect(result.current.draft?.scenes[0].utterances[0].text).toBe("agent 改写的台词");
    save.mockResolvedValue(agentEdited);
    await act(async () => {
      await result.current.save();
    });
    expect(save.mock.calls[1][3]).toBe("fp3");
  });
});
