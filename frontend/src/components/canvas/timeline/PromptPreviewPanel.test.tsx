import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PromptPreviewPanel } from "./PromptPreviewPanel";
import { API } from "@/api";
import { copyText } from "@/utils/clipboard";
import type { ItemPromptPreview } from "@/types";

vi.mock("@/utils/clipboard", () => ({ copyText: vi.fn(() => Promise.resolve()) }));

function preview(overrides: Partial<ItemPromptPreview> = {}): ItemPromptPreview {
  return {
    item_id: "E1S01",
    content_mode: "narration",
    storyboard_image: { text: "Style: Anime\n\n最终分镜图提示词", unavailable: null, is_text_form: false },
    video: { text: null, unavailable: "该分镜还没有填写提示词", is_text_form: false },
    ...overrides,
  };
}

function renderPanel(side: "storyboard_image" | "video" = "storyboard_image", dirty = false) {
  return render(
    <PromptPreviewPanel
      projectName="demo"
      scriptFile="episode_1.json"
      segmentId="E1S01"
      side={side}
      dirty={dirty}
    />,
  );
}

describe("PromptPreviewPanel", () => {
  it("展开时才取预览，展示该侧最终文本", async () => {
    const spy = vi.spyOn(API, "previewScriptItemPrompts").mockResolvedValue(preview());
    renderPanel();

    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /预览最终提示词/ }));

    expect(await screen.findByText(/最终分镜图提示词/)).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith("demo", "E1S01", "episode_1.json", expect.anything());
  });

  it("一键复制交付的是最终文本本身", async () => {
    vi.spyOn(API, "previewScriptItemPrompts").mockResolvedValue(preview());
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /预览最终提示词/ }));
    await screen.findByText(/最终分镜图提示词/);

    fireEvent.click(screen.getByRole("button", { name: "复制最终提示词" }));

    expect(copyText).toHaveBeenCalledWith("Style: Anime\n\n最终分镜图提示词");
  });

  it("该侧不可用时展示后端给的原因，不展示复制入口", async () => {
    vi.spyOn(API, "previewScriptItemPrompts").mockResolvedValue(preview());
    renderPanel("video");
    fireEvent.click(screen.getByRole("button", { name: /预览最终提示词/ }));

    expect(await screen.findByText("该分镜还没有填写提示词")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制最终提示词" })).not.toBeInTheDocument();
  });

  it("草稿脏时提示预览仍按已保存内容渲染", async () => {
    vi.spyOn(API, "previewScriptItemPrompts").mockResolvedValue(preview());
    renderPanel("storyboard_image", true);
    fireEvent.click(screen.getByRole("button", { name: /预览最终提示词/ }));

    expect(await screen.findByText(/有未保存的修改/)).toBeInTheDocument();
  });

  it("请求失败时展示错误，重新渲染可重试", async () => {
    const spy = vi
      .spyOn(API, "previewScriptItemPrompts")
      .mockRejectedValueOnce(new Error("渲染失败"))
      .mockResolvedValue(preview());
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /预览最终提示词/ }));
    expect(await screen.findByText("渲染失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新渲染" }));

    expect(await screen.findByText(/最终分镜图提示词/)).toBeInTheDocument();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
