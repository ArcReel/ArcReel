import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { TextTierFields, type TextTierValue } from "./TextTierFields";

const EMPTY: TextTierValue = { default: "", simple: "", complex: "" };
const OPTIONS = ["gemini/g25", "ark/qwen"];
const PROVIDER_NAMES = { gemini: "Gemini", ark: "Ark" };

/** Subtiers live in collapsible regions; expand before testing. */
async function expandTiers(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText("按用途指定模型"));
}

describe("TextTierFields", () => {
  it("keeps the default model dropdown resident and collapses the two tiers behind 按用途指定模型", async () => {
    const user = userEvent.setup();
    render(
      <TextTierFields
        value={EMPTY}
        onChange={() => {}}
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        defaultLabel="自动选择"
      />,
    );
    // Primary default-model selects persist.
    expect(screen.getByRole("combobox", { name: "默认模型" })).toBeInTheDocument();
    // Agent-provider boundary explanations persist outside collapsible regions.
    expect(screen.getByText(/Agent 供应商/)).toBeInTheDocument();

    await expandTiers(user);
    expect(screen.getByRole("combobox", { name: "简单任务" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "复杂任务" })).toBeInTheDocument();
    // Simple captions require image inputs; complex captions list covered call sites.
    expect(screen.getByText(/图像输入/)).toBeInTheDocument();
    expect(screen.getByText(/脚本生成/)).toBeInTheDocument();
  });

  it("shows the resolved fallback value inside each empty tier (project-priority chain)", async () => {
    const user = userEvent.setup();
    render(
      <TextTierFields
        value={EMPTY}
        onChange={() => {}}
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        defaultLabel="使用全局默认"
        fallbacks={{ default: "gemini/g25", simple: "gemini/g25", complex: "gemini/g25" }}
      />,
    );
    // Empty defaults inherit effective values globally.
    expect(screen.getByRole("combobox", { name: "默认模型" })).toHaveTextContent(
      /跟随全局默认 · Gemini · g25/,
    );

    await expandTiers(user);
    // Subtiers inherit same-layer defaults, worded Follow default.
    for (const name of ["简单任务", "复杂任务"]) {
      expect(screen.getByRole("combobox", { name })).toHaveTextContent(/跟随默认 · Gemini · g25/);
    }
  });

  it("auto-expands the tier section when a tier is already set", () => {
    render(
      <TextTierFields
        value={{ ...EMPTY, complex: "ark/qwen" }}
        onChange={() => {}}
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        defaultLabel="使用全局默认"
      />,
    );
    expect(screen.getByRole("combobox", { name: "复杂任务" })).toHaveTextContent(/qwen/);
    expect(screen.getByText("已指定 1 项")).toBeInTheDocument();
  });

  it("renders only the default model when showTiers is false (creation wizard)", () => {
    render(
      <TextTierFields
        value={EMPTY}
        onChange={() => {}}
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        defaultLabel="使用全局默认"
        showTiers={false}
      />,
    );
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    expect(screen.queryByText("按用途指定模型")).not.toBeInTheDocument();
  });

  it("calls onChange writing only the edited tier key", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TextTierFields
        value={EMPTY}
        onChange={onChange}
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        defaultLabel="自动选择"
      />,
    );
    await expandTiers(user);
    await user.click(screen.getByRole("combobox", { name: "简单任务" }));
    await screen.findByRole("listbox");
    await user.click(screen.getByRole("option", { name: /g25/ }));
    expect(onChange).toHaveBeenCalledWith({ default: "", simple: "gemini/g25", complex: "" });
  });
});
