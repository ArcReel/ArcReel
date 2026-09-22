import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { PromptTemplateMeta } from "@/types";
import { PromptTemplateList } from "./PromptTemplateList";

function meta(overrides: Partial<PromptTemplateMeta> & Pick<PromptTemplateMeta, "id">): PromptTemplateMeta {
  return {
    category: "text",
    title: overrides.id,
    description: "",
    applies_to: {},
    slots: {},
    protected: false,
    output_schema: null,
    ...overrides,
  };
}

const STYLE_A = meta({ id: "style/ghibli", category: "style", title: "吉卜力" });
const STYLE_B = meta({ id: "style/arcane", category: "style", title: "双城之战" });
const LAB = meta({ id: "lab/draft", category: "lab", title: "实验草稿" });
const VIDEO = meta({ id: "video/unit", category: "video", title: "视频单元" });
const ASSET = meta({ id: "asset/sheet", category: "asset", title: "资产设定图" });
const DRAMA_PLAN = meta({
  id: "text/drama_script_plan",
  title: "剧情脚本规划",
  applies_to: { content_mode: ["drama"], generation_mode: ["storyboard"] },
});
const NARRATION_PLAN = meta({
  id: "text/narration_script_plan",
  title: "旁白脚本规划",
  applies_to: { content_mode: ["narration"], generation_mode: ["storyboard"] },
});
const OVERVIEW = meta({
  id: "text/source_overview",
  title: "源文总览",
  applies_to: { source_kind: ["novel", "screenplay"] },
});
const STORYBOARD = meta({ id: "storyboard/image", category: "storyboard", title: "分镜图" });

const TEMPLATES = [STYLE_A, LAB, VIDEO, ASSET, DRAMA_PLAN, STYLE_B, NARRATION_PLAN, OVERVIEW, STORYBOARD];

describe("PromptTemplateList", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "listPromptTemplates").mockResolvedValue({ templates: TEMPLATES });
  });

  it("orders categories along the pipeline regardless of response order, unknown last", async () => {
    render(<PromptTemplateList onSelect={vi.fn()} />);

    const headings = await screen.findAllByRole("heading", { level: 3 });
    expect(headings.map((h) => h.textContent)).toEqual([
      "文本生成",
      "资产图",
      "分镜图",
      "视频",
      "画风",
      "lab",
    ]);
  });

  it("keeps the style group collapsed with its count until expanded into a compact list", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<PromptTemplateList onSelect={onSelect} />);

    const styleGroup = await screen.findByRole("region", { name: "画风" });
    expect(within(styleGroup).getByText("2 个模版")).toBeInTheDocument();
    expect(within(styleGroup).queryByText("吉卜力")).not.toBeInTheDocument();

    await user.click(within(styleGroup).getByRole("button", { name: "画风" }));
    await user.click(within(styleGroup).getByRole("button", { name: "双城之战" }));
    expect(onSelect).toHaveBeenCalledWith("style/arcane");

    await user.click(within(styleGroup).getByRole("button", { name: "画风" }));
    expect(within(styleGroup).queryByText("吉卜力")).not.toBeInTheDocument();
  });

  it("filters by an axis value, keeping templates that do not declare that axis", async () => {
    const user = userEvent.setup();
    render(<PromptTemplateList onSelect={vi.fn()} />);

    const contentMode = await screen.findByRole("group", { name: "创作类型" });
    expect(
      within(contentMode).getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["全部", "剧情演绎", "旁白/解说"]);
    expect(screen.getByRole("group", { name: "生成模式" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "源文件类型" })).toBeInTheDocument();

    await user.click(within(contentMode).getByRole("button", { name: "旁白/解说" }));

    expect(within(contentMode).getByRole("button", { name: "旁白/解说" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const textGroup = screen.getByRole("region", { name: "文本生成" });
    expect(within(textGroup).queryByText("剧情脚本规划")).not.toBeInTheDocument();
    expect(within(textGroup).getByText("旁白脚本规划")).toBeInTheDocument();
    expect(within(textGroup).getByText("源文总览")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "资产图" })).getByText("资产设定图")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "画风" })).toBeInTheDocument();

    await user.click(within(contentMode).getByRole("button", { name: "全部" }));
    expect(within(textGroup).getByText("剧情脚本规划")).toBeInTheDocument();
  });

  it("hides the filter bar when no template declares a filterable axis", async () => {
    vi.spyOn(API, "listPromptTemplates").mockResolvedValue({ templates: [ASSET, STYLE_A] });
    render(<PromptTemplateList onSelect={vi.fn()} />);

    await screen.findByRole("region", { name: "资产图" });
    expect(screen.queryByRole("group", { name: "创作类型" })).not.toBeInTheDocument();
  });
});
