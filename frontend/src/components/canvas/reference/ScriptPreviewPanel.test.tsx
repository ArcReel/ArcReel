import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ScriptPreviewPanel } from "./ScriptPreviewPanel";
import { API } from "@/api";
import type { MentionLookup } from "@/hooks/useShotPromptHighlight";
import type { ScriptPreview } from "@/types";

const LOOKUP: MentionLookup = { 张三: "character", 酒馆: "scene" };

function mkPreview(overrides: Partial<ScriptPreview> = {}): ScriptPreview {
  return {
    shots: [{ index: 1, text: "中景。" }],
    references: [{ type: "scene", name: "酒馆" }],
    utterances: [{ shot_index: 1, kind: "dialogue", speaker: "张三", text: "我来了" }],
    warnings: [],
    ...overrides,
  };
}

function renderPanel(text: string) {
  return render(
    <ScriptPreviewPanel projectName="demo" episode={1} text={text} lookup={LOOKUP} />,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ScriptPreviewPanel", () => {
  it("renders derived references, utterance counts, and warnings from the server", async () => {
    const spy = vi.spyOn(API, "previewReferenceScript").mockResolvedValue(
      mkPreview({
        utterances: [
          { shot_index: 1, kind: "dialogue", speaker: "张三", text: "我来了" },
          { shot_index: 1, kind: "voiceover", speaker: null, text: "那年冬天格外冷" },
        ],
        warnings: [{ key: "ref_warn_speaker_without_audio", message: "角色「张三」未设置参考音频" }],
      }),
    );

    renderPanel("镜头1：@酒馆 内景。\n@[张三]：{我来了}\n{那年冬天格外冷}");
    await vi.advanceTimersByTimeAsync(500);

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await screen.findByText("酒馆");
    expect(screen.getByText("1 句台词 · 1 段画外音")).toBeTruthy();
    expect(screen.getByText("角色「张三」未设置参考音频")).toBeTruthy();
  });

  it("renders the highlighted script without waiting for the request", () => {
    vi.spyOn(API, "previewReferenceScript").mockImplementation(() => new Promise(() => {}));
    renderPanel("镜头1：中景。\n@[张三]：{我来了}");
    expect(screen.getByText("我来了")).toBeTruthy();
  });

  it("debounces edits into a single request", async () => {
    const spy = vi.spyOn(API, "previewReferenceScript").mockResolvedValue(mkPreview());
    const { rerender } = renderPanel("镜头1：中");
    rerender(<ScriptPreviewPanel projectName="demo" episode={1} text="镜头1：中景" lookup={LOOKUP} />);
    rerender(<ScriptPreviewPanel projectName="demo" episode={1} text="镜头1：中景。" lookup={LOOKUP} />);
    await vi.advanceTimersByTimeAsync(500);

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith("demo", 1, "镜头1：中景。", expect.anything());
  });

  it("lists the server-derived utterances alongside the local highlight", async () => {
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue(
      mkPreview({
        utterances: [
          { shot_index: 2, kind: "dialogue", speaker: "张三", text: "我来了" },
          { shot_index: 2, kind: "voiceover", speaker: null, text: "那年冬天格外冷" },
        ],
      }),
    );

    renderPanel("镜头1：中景。\n镜头2：\n@[张三]：{我来了}\n{那年冬天格外冷}");
    await vi.advanceTimersByTimeAsync(500);

    // 每条 utterance 一行：镜头号 + 说话人（画外音无名） + 正文
    expect(await screen.findAllByText("镜头 2")).toHaveLength(2);
    expect(screen.getAllByText("我来了")).toHaveLength(2); // 本地高亮 + 服务端派生行
    expect(screen.getAllByText("那年冬天格外冷")).toHaveLength(2);
  });

  it("aborts an in-flight request as soon as the script changes", async () => {
    const signals: (AbortSignal | undefined)[] = [];
    vi.spyOn(API, "previewReferenceScript").mockImplementation((_p, _e, _t, opts) => {
      signals.push(opts?.signal);
      return new Promise(() => {});
    });

    const { rerender } = renderPanel("镜头1：中景。");
    await vi.advanceTimersByTimeAsync(500);
    expect(signals).toHaveLength(1);
    expect(signals[0]?.aborted).toBe(false);

    // 编辑发生在下一次 debounce 到点之前：在途请求须立刻作废，不能等 400ms
    rerender(<ScriptPreviewPanel projectName="demo" episode={1} text="镜头1：中景，改了" lookup={LOOKUP} />);
    expect(signals[0]?.aborted).toBe(true);
  });

  it("marks the derived panel stale while it catches up with an edit", async () => {
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue(
      mkPreview({ warnings: [{ key: "ref_warn_silent_model", message: "当前模型不发声" }] }),
    );
    const { rerender } = renderPanel("镜头1：中景。");
    await vi.advanceTimersByTimeAsync(500);
    const warnList = await screen.findByRole("list", { name: /解析提示|Parse notices/ });
    expect(warnList.getAttribute("aria-busy")).toBeNull();

    // 编辑后旧派生还在屏上，但必须标成过期——否则会被当成当前正文的解析结果
    rerender(<ScriptPreviewPanel projectName="demo" episode={1} text="镜头1：中景，改了" lookup={LOOKUP} />);
    expect(warnList.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("当前模型不发声")).toBeTruthy();

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(warnList.getAttribute("aria-busy")).toBeNull());
  });

  it("refetches when the project assets behind the warnings change", async () => {
    const spy = vi.spyOn(API, "previewReferenceScript").mockResolvedValue(mkPreview());
    const { rerender } = renderPanel("镜头1：@酒馆 内景。");
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    // 资产登记后 lookup 换新：未登记 mention / 未设参考音频等 warning 读的是项目资产表，
    // 不重拉就会一直挂着已经不成立的提示。
    rerender(
      <ScriptPreviewPanel
        projectName="demo"
        episode={1}
        text="镜头1：@酒馆 内景。"
        lookup={{ ...LOOKUP, 王五: "character" }}
      />,
    );
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });

  it("surfaces a failed preview request instead of showing stale derivations", async () => {
    const spy = vi
      .spyOn(API, "previewReferenceScript")
      .mockResolvedValueOnce(mkPreview())
      .mockRejectedValue(new Error("boom"));
    const { rerender } = renderPanel("镜头1：中景。");
    await vi.advanceTimersByTimeAsync(500);
    await screen.findByText("酒馆");

    rerender(<ScriptPreviewPanel projectName="demo" episode={1} text="镜头1：中景，改了" lookup={LOOKUP} />);
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("boom");
    // 旧派生随之清空，报错横幅下不再留着对不上正文的参考图
    await waitFor(() => expect(screen.queryByText("酒馆")).toBeNull());
  });
});
