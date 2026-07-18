import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { TextTierFields, type TextTierValue } from "./TextTierFields";

const EMPTY: TextTierValue = { default: "", simple: "", complex: "" };
const OPTIONS = ["gemini/g25", "ark/qwen"];
const PROVIDER_NAMES = { gemini: "Gemini", ark: "Ark" };

describe("TextTierFields", () => {
  it("renders three tier dropdowns with labels, captions, and the agent-provider boundary note", () => {
    render(
      <TextTierFields
        value={EMPTY}
        onChange={() => {}}
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        defaultLabel="自动选择"
      />,
    );
    // 默认 / 简单 / 复杂 三档下拉
    expect(screen.getAllByRole("combobox")).toHaveLength(3);
    expect(screen.getByText("默认模型")).toBeInTheDocument();
    expect(screen.getByText("简单任务")).toBeInTheDocument();
    expect(screen.getByText("复杂任务")).toBeInTheDocument();
    // 简单档 caption 注明需图像输入
    expect(screen.getByText(/图像输入/)).toBeInTheDocument();
    // 复杂档 caption 列出覆盖调用点
    expect(screen.getByText(/剧本生成/)).toBeInTheDocument();
    // 卡片底部 Agent 供应商边界说明
    expect(screen.getByText(/智能体供应商/)).toBeInTheDocument();
  });

  it("shows the resolved fallback value inside each empty tier (project-priority chain)", () => {
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
    // 三档均留空 → 每档触发按钮以「跟随全局默认 · 生效值」呈现继承结果
    const followers = screen.getAllByText(/跟随全局默认/);
    expect(followers).toHaveLength(3);
    expect(followers[0].textContent).toMatch(/Gemini.*g25/);
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
    // 第二个下拉是「简单任务」
    await user.click(screen.getAllByRole("combobox")[1]);
    await user.click(screen.getByRole("option", { name: /g25/ }));
    expect(onChange).toHaveBeenCalledWith({ default: "", simple: "gemini/g25", complex: "" });
  });
});
