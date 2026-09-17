import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { PromptTemplateDetail, PromptTemplateMeta } from "@/types";
import { PromptTemplatesSection } from "./PromptTemplatesSection";

function meta(overrides: Partial<PromptTemplateMeta>): PromptTemplateMeta {
  return {
    id: "asset/sheet",
    category: "asset",
    title: "资产图",
    description: "角色、场景与道具资产图。",
    applies_to: {},
    slots: {},
    protected: false,
    output_schema: null,
    ...overrides,
  };
}

const ASSET_SHEET = meta({
  applies_to: { asset_type: ["character", "scene"] },
  slots: { asset_type: "资产类型", description: "外观描述" },
});
const ASSET_ICON = meta({ id: "asset/icon", title: "资产图标", description: "小尺寸图标。" });
const DRAFT = meta({
  id: "lab/draft",
  category: "lab",
  title: "实验草稿",
  description: "未知类别按原值分组。",
  output_schema: "lib.script_models:ImagePrompt",
});

const SHEET_DETAIL: PromptTemplateDetail = {
  template: ASSET_SHEET,
  source: '{{ variant("asset/sheet/title", asset_type) }}\n\n{{ description }}',
  partials: [
    { name: "asset/sheet/title/character", source: "角色设定图，三视图" },
    { name: "asset/sheet/title/scene", source: "" },
  ],
};

const DRAFT_DETAIL: PromptTemplateDetail = {
  template: DRAFT,
  source: "固定正文",
  partials: [],
  output_schema: {
    type: "object",
    properties: {
      shot_type: { enum: ["Close-up", "Long Shot"], type: "string", description: "镜头类型" },
    },
  },
};

describe("PromptTemplatesSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "listPromptTemplates").mockResolvedValue({
      templates: [ASSET_SHEET, DRAFT, ASSET_ICON],
    });
  });

  it("groups templates by the category the registry returns, keeping first-seen order", async () => {
    render(<PromptTemplatesSection />);

    const headings = await screen.findAllByRole("heading", { level: 3 });
    expect(headings.map((h) => h.textContent)).toEqual(["资产图", "lab"]);

    const assetGroup = screen.getByRole("region", { name: "资产图" });
    expect(within(assetGroup).getAllByRole("button").map((b) => b.textContent)).toEqual([
      expect.stringContaining("角色、场景与道具资产图。"),
      expect.stringContaining("小尺寸图标。"),
    ]);
    expect(within(screen.getByRole("region", { name: "lab" })).getByText("实验草稿")).toBeInTheDocument();
  });

  it("opens a template to show axes, slots, marked source and every partial body", async () => {
    const user = userEvent.setup();
    const getDetail = vi.spyOn(API, "getPromptTemplate").mockResolvedValue(SHEET_DETAIL);
    render(<PromptTemplatesSection />);

    await user.click(await screen.findByRole("button", { name: /角色、场景与道具资产图。/ }));

    expect(getDetail).toHaveBeenCalledWith("asset/sheet", expect.anything());
    expect(await screen.findByRole("heading", { level: 2, name: "资产图" })).toBeInTheDocument();
    expect(screen.getByText("character")).toBeInTheDocument();
    expect(screen.getByText("{{ description }}", { selector: "dt" })).toBeInTheDocument();
    expect(screen.getByText("外观描述")).toBeInTheDocument();
    expect(screen.getByText("{{ description }}", { selector: "mark" })).toBeInTheDocument();
    expect(
      screen.getByText('{{ variant("asset/sheet/title", asset_type) }}', { selector: "mark" }),
    ).toBeInTheDocument();
    expect(screen.getByText("asset/sheet/title/scene")).toBeInTheDocument();
    expect(screen.getByText("角色设定图，三视图")).toBeVisible();
    expect(screen.getByText("空片段：该取值下不追加措辞。")).toBeInTheDocument();
    expect(screen.queryByText("输出结构")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回模版列表" }));
    expect(await screen.findByRole("region", { name: "资产图" })).toBeInTheDocument();
  });

  it("keeps the output structure collapsed until expanded", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getPromptTemplate").mockResolvedValue(DRAFT_DETAIL);
    const { container } = render(<PromptTemplatesSection />);

    await user.click(await screen.findByRole("button", { name: /实验草稿/ }));

    await screen.findByText("输出结构");
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("shot_type")).not.toBeVisible();

    await user.click(screen.getByText("输出结构"));
    expect(details?.open).toBe(true);
    expect(screen.getByText("shot_type")).toBeVisible();
    expect(screen.getByText("Long Shot")).toBeVisible();
    expect(screen.getByText("镜头类型")).toBeVisible();
  });

  it("shows an error state for the list and recovers on retry", async () => {
    const user = userEvent.setup();
    const list = vi
      .spyOn(API, "listPromptTemplates")
      .mockRejectedValueOnce(new Error("网络中断"))
      .mockResolvedValueOnce({ templates: [ASSET_SHEET] });
    render(<PromptTemplatesSection />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载提示词模版失败");
    expect(alert).toHaveTextContent("网络中断");

    await user.click(within(alert).getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("region", { name: "资产图" })).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("shows an error state when the detail fails to load", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getPromptTemplate").mockRejectedValue(new Error("模版不存在"));
    render(<PromptTemplatesSection />);

    await user.click(await screen.findByRole("button", { name: /资产图标/ }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载模版详情失败");
    expect(alert).toHaveTextContent("模版不存在");
  });
});
