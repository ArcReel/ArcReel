import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { PromptTemplateSource } from "./PromptTemplateSource";
import type { PromptTemplateMeta } from "@/types";

const template: PromptTemplateMeta = {
  id: "asset/sheet",
  category: "asset",
  title: "资产图",
  description: "资产图模版",
  stage: "asset_sheet",
  invoked_by: { kind: "generation_task", name: "asset" },
  applies_to: { asset_type: ["scene", "character"] },
  slots: { description: "外观描述", instructions: "附加指令", assets: "资产清单" },
  protected: false,
  output_schema: null,
};

describe("PromptTemplateSource", () => {
  it("expands nested partials and switches only the chosen reference from the first declared axis value", async () => {
    const user = userEvent.setup();
    render(<PromptTemplateSource
      template={template}
      text={'{{ variant("asset/sheet/title", asset_type) }}\n{{ variant("asset/sheet/title", asset_type) }}'}
      partials={[
        { name: "asset/sheet/title/character", source: "角色三视图正文", protected: false, referenced_by: ["asset/sheet"] },
        { name: "asset/sheet/title/scene", source: '场景全景正文 {{ partial("shared/style", instructions=instructions) }}', protected: false, referenced_by: ["asset/sheet"] },
        { name: "shared/style", source: '画风说明 {{ partial("shared/detail") }}', protected: false, referenced_by: ["asset/sheet"] },
        { name: "shared/detail", source: "深层片段正文", protected: false, referenced_by: ["asset/sheet"] },
      ]}
    />);
    const references = screen.getAllByRole("button", { name: '{{ variant("asset/sheet/title", asset_type) }}' });
    expect(screen.queryByText(/场景全景正文/)).not.toBeInTheDocument();
    await user.click(references[0]);
    await user.click(references[1]);
    expect(screen.getAllByText("asset/sheet/title/scene")).toHaveLength(2);
    expect(screen.getAllByText(/场景全景正文/)).toHaveLength(2);
    expect(screen.queryByText("角色三视图正文")).not.toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: '{{ partial("shared/style", instructions=instructions) }}' })[1]);
    expect(screen.getByText(/画风说明/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: '{{ partial("shared/detail") }}' }));
    expect(screen.getByText("深层片段正文")).toBeVisible();

    const selectors = screen.getAllByRole("combobox", { name: "资产类型" });
    expect(selectors[0]).toHaveValue("scene");
    await user.selectOptions(selectors[0], "character");
    expect(screen.getByText("角色三视图正文")).toBeVisible();
    expect(screen.getAllByText(/场景全景正文/)).toHaveLength(1);
    expect(selectors[1]).toHaveValue("scene");
    expect(screen.getByText("深层片段正文")).toBeVisible();

    await user.click(references[1]);
    expect(references[1]).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("深层片段正文")).not.toBeInTheDocument();
    expect(screen.getByText("角色三视图正文")).toBeVisible();
  });

  it("shows intentional blank variants and leaves filtered expressions as source", async () => {
    const user = userEvent.setup();
    render(<PromptTemplateSource
      template={template}
      text={'{{ variant("asset/sheet/title", asset_type) }}\n{{ partial("shared/style") | indent(2) }}'}
      partials={[
        { name: "asset/sheet/title/scene", source: "", protected: false, referenced_by: ["asset/sheet"] },
        { name: "asset/sheet/title/character", source: "角色正文", protected: false, referenced_by: ["asset/sheet"] },
        { name: "shared/style", source: "画风正文", protected: false, referenced_by: ["asset/sheet"] },
      ]}
    />);

    await user.click(screen.getByRole("button", { name: '{{ variant("asset/sheet/title", asset_type) }}' }));
    expect(screen.getByText("空片段：该取值下不追加措辞。")).toBeVisible();
    expect(screen.getByText('{{ partial("shared/style") | indent(2) }}')).toBeVisible();
    expect(screen.queryByRole("button", { name: /indent/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox"), "character");
    expect(screen.getByText("角色正文")).toBeVisible();
    expect(screen.queryByText("空片段：该取值下不追加措辞。")).not.toBeInTheDocument();
  });

  it("marks nested optional ranges and keeps else branches and filters verbatim", () => {
    render(<PromptTemplateSource
      template={template}
      partials={[]}
      text={'前文{% if instructions %}可选内容{% if assets %}{{ assets | join(", ") }}{% else %}没有资产{% endif %}{% endif %}后文'}
    />);

    const outer = screen.getByRole("group", { name: "仅当 instructions 存在" });
    const inner = within(outer).getByRole("group", { name: "仅当 assets 存在" });
    expect(within(outer).getByText("{% if instructions %}")).toBeVisible();
    expect(within(inner).getByText('{{ assets | join(", ") }}')).toBeVisible();
    expect(within(outer).getByText("{% else %}")).toBeVisible();
    expect(outer).toHaveTextContent("没有资产");
    expect(inner).not.toHaveTextContent("没有资产");
    expect(outer).not.toHaveTextContent("前文");
    expect(outer).not.toHaveTextContent("后文");
  });

  it("renders only declared slots as chips and keeps loop variables verbatim", () => {
    render(<PromptTemplateSource
      template={template}
      partials={[]}
      text={'{{ description }}{% for name in assets %}「{{ name }}」{% endfor %}'}
    />);

    expect(screen.getByText("description")).toHaveAttribute("title", "外观描述");
    expect(screen.getByText("{{ name }}")).toBeVisible();
  });
});
