import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PromptPreviewButton } from "./PromptPreviewButton";
import { copyText } from "@/utils/clipboard";
import type { RenderedPromptPreview } from "@/types";

vi.mock("@/utils/clipboard", () => ({ copyText: vi.fn(() => Promise.resolve()) }));

function rendered(overrides: Partial<RenderedPromptPreview> = {}): RenderedPromptPreview {
  return { text: "Style: Anime\n\n最终提示词", unavailable: null, is_text_form: false, warnings: [], ...overrides };
}

function openPreview() {
  fireEvent.click(screen.getByRole("button", { name: "查看提示词" }));
  return screen.getByRole("dialog", { name: "分镜图最终提示词" });
}

describe("PromptPreviewButton", () => {
  it("打开弹窗才请求，展示最终文本与口径说明", async () => {
    const load = vi.fn().mockResolvedValue(rendered());
    render(<PromptPreviewButton title="分镜图最终提示词" load={load} notice="按已保存内容渲染" />);

    expect(load).not.toHaveBeenCalled();
    const dialog = openPreview();

    expect(await within(dialog).findByText(/最终提示词$/)).toBeInTheDocument();
    expect(within(dialog).getByText("按已保存内容渲染")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("一键复制交付的是最终文本本身", async () => {
    render(<PromptPreviewButton title="分镜图最终提示词" load={() => Promise.resolve(rendered())} />);
    const dialog = openPreview();
    await within(dialog).findByText(/最终提示词$/);

    fireEvent.click(within(dialog).getByRole("button", { name: "复制最终提示词" }));

    expect(await within(dialog).findByRole("button", { name: "已复制" })).toBeInTheDocument();
    expect(copyText).toHaveBeenCalledWith("Style: Anime\n\n最终提示词");
  });

  it("渲染时的提示随文本一起展示，用户在生成前就能看到参考图会被裁剪", async () => {
    const load = () => Promise.resolve(rendered({ warnings: ["参考图数量 8 超出 gpt-image-2 上限 7，已取前 7 张"] }));
    render(<PromptPreviewButton title="分镜图最终提示词" load={load} />);
    const dialog = openPreview();

    expect(await within(dialog).findByText(/最终提示词$/)).toBeInTheDocument();
    expect(within(dialog).getByRole("list", { name: "生成提示" })).toHaveTextContent(
      "参考图数量 8 超出 gpt-image-2 上限 7，已取前 7 张",
    );
  });

  it("不可用时展示后端给的原因，不展示复制入口", async () => {
    const load = () => Promise.resolve(rendered({ text: null, unavailable: "该分镜还没有填写提示词" }));
    render(<PromptPreviewButton title="分镜图最终提示词" load={load} />);
    const dialog = openPreview();

    expect(await within(dialog).findByText("该分镜还没有填写提示词")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "复制最终提示词" })).not.toBeInTheDocument();
  });

  it("请求失败时展示错误，重新渲染可重试", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("渲染失败")).mockResolvedValue(rendered());
    render(<PromptPreviewButton title="分镜图最终提示词" load={load} />);
    const dialog = openPreview();
    expect(await within(dialog).findByText("渲染失败")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "重新渲染" }));

    expect(await within(dialog).findByText(/最终提示词$/)).toBeInTheDocument();
    expect(within(dialog).queryByText("渲染失败")).not.toBeInTheDocument();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it("关闭后再打开重新请求，拿到的是调用方最新的内容", async () => {
    const { rerender } = render(
      <PromptPreviewButton title="分镜图最终提示词" load={() => Promise.resolve(rendered({ text: "旧草稿" }))} />,
    );
    const first = openPreview();
    expect(await within(first).findByText("旧草稿")).toBeInTheDocument();
    fireEvent.click(within(first).getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    rerender(
      <PromptPreviewButton title="分镜图最终提示词" load={() => Promise.resolve(rendered({ text: "新草稿" }))} />,
    );
    const second = openPreview();

    expect(await within(second).findByText("新草稿")).toBeInTheDocument();
    expect(within(second).queryByText("旧草稿")).not.toBeInTheDocument();
  });

  it("关闭弹窗会中止在途请求", () => {
    let signal: AbortSignal | undefined;
    const load = vi.fn((nextSignal: AbortSignal) => {
      signal = nextSignal;
      return new Promise<RenderedPromptPreview>(() => undefined);
    });
    render(<PromptPreviewButton title="分镜图最终提示词" load={load} />);
    const dialog = openPreview();

    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));

    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("调用方可在文本下方追加本次结果的附加信息", async () => {
    type WithRefs = RenderedPromptPreview & { references: string[] };
    const load = () => Promise.resolve<WithRefs>({ ...rendered(), references: ["角色A.png", "场景B.png"] });
    render(
      <PromptPreviewButton
        title="分镜图最终提示词"
        load={load}
        renderExtra={(result) => <ul aria-label="参考图">{result.references.map((r) => <li key={r}>{r}</li>)}</ul>}
      />,
    );
    const dialog = openPreview();

    expect(await within(dialog).findByRole("list", { name: "参考图" })).toHaveTextContent("角色A.png");
  });
});
