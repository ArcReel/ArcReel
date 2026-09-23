import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import type { PromptTemplateDetail, PromptTemplateMeta, PromptTemplatePartial } from "@/types";
import { PromptTemplatesSection } from "./PromptTemplatesSection";

function renderSection(path = "/app/settings?section=prompt-templates") {
  const location = memoryLocation({ path, record: true });
  const view = render(
    <Router hook={location.hook}>
      <PromptTemplatesSection />
    </Router>,
  );
  const params = () => new URLSearchParams(location.history!.at(-1)!.split("?")[1]);
  return { ...view, location, params };
}

function meta(overrides: Partial<PromptTemplateMeta>): PromptTemplateMeta {
  return {
    id: "asset/sheet",
    category: "asset",
    title: "资产图",
    description: "角色、场景与道具资产图。",
    stage: "asset_sheet",
    invoked_by: { kind: "generation_task", name: "asset" },
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
  output_schema: "lib.script.script_models:ImagePrompt",
});

const SHEET_DETAIL: PromptTemplateDetail = {
  template: ASSET_SHEET,
  source: '{{ variant("asset/sheet/title", asset_type) }}\n\n{{ description }}',
  partials: [
    { name: "asset/sheet/title/character", source: "角色设定图，三视图", protected: false, referenced_by: ["asset/sheet"] },
    { name: "asset/sheet/title/scene", source: "", protected: false, referenced_by: ["asset/sheet"] },
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

const LOCKED_SHEET = meta({ protected: true });
const SHARED_AVOID: PromptTemplatePartial = {
  name: "shared/avoid",
  source: "Avoid: 水印",
  protected: true,
  referenced_by: ["asset/sheet", "asset/icon"],
};
const LOCKED_SHEET_DETAIL: PromptTemplateDetail = {
  template: LOCKED_SHEET,
  source: '{{ partial("shared/avoid") }}',
  partials: [SHARED_AVOID],
};
const ICON_DETAIL: PromptTemplateDetail = { template: ASSET_ICON, source: "图标正文", partials: [] };

describe("PromptTemplatesSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, "", "/app/settings?section=prompt-templates");
    vi.spyOn(API, "listPromptTemplates").mockResolvedValue({
      templates: [ASSET_SHEET, DRAFT, ASSET_ICON],
    });
  });

  it("opens a template and expands the selected partial inline", async () => {
    const user = userEvent.setup();
    const getDetail = vi.spyOn(API, "getPromptTemplate").mockResolvedValue(SHEET_DETAIL);
    renderSection();

    await user.click(await screen.findByRole("button", { name: /角色、场景与道具资产图。/ }));

    expect(getDetail).toHaveBeenCalledWith("asset/sheet", expect.anything());
    expect(await screen.findByRole("heading", { level: 2, name: "资产图" })).toBeInTheDocument();
    expect(screen.getByText("character")).toBeInTheDocument();
    expect(screen.getByText("description", { selector: "dt" })).toBeInTheDocument();
    expect(screen.getByText("外观描述")).toBeInTheDocument();
    expect(screen.getByTitle("外观描述")).toHaveTextContent("description");
    expect(screen.getByRole("heading", { name: "模版正文" })).toBeInTheDocument();
    expect(screen.queryByText("引用的片段")).not.toBeInTheDocument();
    expect(screen.queryByText("角色设定图，三视图")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: '{{ variant("asset/sheet/title", asset_type) }}' }));
    expect(screen.getByText("角色设定图，三视图")).toBeVisible();
    expect(screen.getByText("asset/sheet/title/character")).toBeVisible();
    expect(screen.queryByText("输出结构")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回模版列表" }));
    expect(await screen.findByRole("region", { name: "资产图" })).toBeInTheDocument();
  });

  it("keeps the output structure collapsed until expanded", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getPromptTemplate").mockResolvedValue(DRAFT_DETAIL);
    const { container } = renderSection();

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
    renderSection();

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
    renderSection();

    await user.click(await screen.findByRole("button", { name: /资产图标/ }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载模版详情失败");
    expect(alert).toHaveTextContent("模版不存在");
  });

  it("opens a partial from its expanded reference and returns to a referencing template through the URL", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getPromptTemplate").mockImplementation(async (id) =>
      id === "asset/icon" ? ICON_DETAIL : LOCKED_SHEET_DETAIL,
    );
    const getPartial = vi.spyOn(API, "getPromptPartial").mockResolvedValue(SHARED_AVOID);
    const params = () => new URLSearchParams(window.location.search);
    render(<PromptTemplatesSection />);

    await user.click(await screen.findByRole("button", { name: /角色、场景与道具资产图。/ }));
    expect(params().get("section")).toBe("prompt-templates");
    expect(params().get("template")).toBe("asset/sheet");
    const header = (await screen.findByRole("heading", { level: 2, name: "资产图" })).closest("header")!;
    expect(within(header).getByText("锁定")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: '{{ partial("shared/avoid") }}' }));
    expect(screen.getByText("Avoid: 水印")).toBeVisible();
    expect(screen.getAllByText("锁定")).toHaveLength(2);
    expect(screen.getByText("2 份模版引用")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "shared/avoid" }));
    expect(params().get("partial")).toBe("shared/avoid");
    expect(params().get("section")).toBe("prompt-templates");
    expect(getPartial).toHaveBeenCalledWith("shared/avoid", expect.anything());
    expect(await screen.findByRole("heading", { level: 2, name: "shared/avoid" })).toBeInTheDocument();
    expect(screen.getByText("锁定")).toBeVisible();
    expect(screen.getByText("Avoid: 水印")).toBeVisible();
    const usages = screen.getByRole("heading", { name: "引用它的模版" }).closest("section")!;
    expect(await within(usages).findByRole("button", { name: /资产图标/ })).toBeInTheDocument();

    await user.click(within(usages).getByRole("button", { name: /资产图标/ }));
    expect(params().get("template")).toBe("asset/icon");
    expect(params().has("partial")).toBe(false);
    expect(await screen.findByRole("heading", { level: 2, name: "资产图标" })).toBeInTheDocument();
    expect(screen.queryByText("锁定")).not.toBeInTheDocument();

    window.history.back();
    await waitFor(() => expect(params().get("partial")).toBe("shared/avoid"));
    expect(await screen.findByRole("heading", { level: 2, name: "shared/avoid" })).toBeInTheDocument();

    window.history.forward();
    await waitFor(() => expect(params().get("template")).toBe("asset/icon"));
    expect(await screen.findByRole("heading", { level: 2, name: "资产图标" })).toBeInTheDocument();
  });

  it("restores a template detail from the URL", async () => {
    vi.spyOn(API, "getPromptTemplate").mockResolvedValue(LOCKED_SHEET_DETAIL);
    renderSection("/app/settings?section=prompt-templates&template=asset%2Fsheet");

    expect(await screen.findByRole("heading", { level: 2, name: "资产图" })).toBeInTheDocument();
  });

  it("restores a partial detail from the URL and goes back to its template", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getPromptPartial").mockResolvedValue({ ...SHARED_AVOID, protected: false });
    vi.spyOn(API, "getPromptTemplate").mockResolvedValue(LOCKED_SHEET_DETAIL);
    const { params } = renderSection(
      "/app/settings?section=prompt-templates&template=asset%2Fsheet&partial=shared%2Favoid",
    );

    expect(await screen.findByRole("heading", { level: 2, name: "shared/avoid" })).toBeInTheDocument();
    expect(screen.queryByText("锁定")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回模版详情" }));
    expect(params().has("partial")).toBe(false);
    expect(params().get("template")).toBe("asset/sheet");
    expect(await screen.findByRole("heading", { level: 2, name: "资产图" })).toBeInTheDocument();
  });

  it("shows an error state when the partial fails to load", async () => {
    vi.spyOn(API, "getPromptPartial").mockRejectedValue(new Error("片段不存在"));
    renderSection("/app/settings?section=prompt-templates&partial=shared%2Fmissing");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载片段详情失败");
    expect(alert).toHaveTextContent("片段不存在");
    expect(screen.getByRole("button", { name: "返回模版列表" })).toBeInTheDocument();
  });
});
