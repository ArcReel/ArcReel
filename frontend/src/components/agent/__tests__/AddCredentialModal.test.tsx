import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PresetProvider } from "@/types/agent-credential";

import { AddCredentialModal } from "../AddCredentialModal";

const presets: PresetProvider[] = [
  {
    id: "deepseek",
    display_name: "DeepSeek",
    icon_key: "DeepSeek",
    messages_url: "https://api.deepseek.com/anthropic",
    discovery_url: "https://api.deepseek.com",
    default_model: "deepseek-chat",
    suggested_models: ["deepseek-chat"],
    docs_url: null,
    api_key_url: "https://platform.deepseek.com/api_keys",
    notes: null,
    api_key_pattern: null,
    is_recommended: true,
  },
];

const presetsWithSecond: PresetProvider[] = [
  ...presets,
  {
    id: "moonshot",
    display_name: "Moonshot",
    icon_key: "Moonshot",
    messages_url: "https://api.moonshot.cn/anthropic",
    discovery_url: "https://api.moonshot.cn",
    default_model: "moonshot-v1",
    suggested_models: ["moonshot-v1"],
    docs_url: null,
    api_key_url: "https://platform.moonshot.cn/api_keys",
    notes: null,
    api_key_pattern: null,
    is_recommended: false,
  },
];

describe("AddCredentialModal", () => {
  it("renders custom config chip first", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const chips = screen.getAllByTestId("preset-chip");
    expect(chips[0]).toHaveTextContent(/custom/i);
  });

  it("when preset chosen, base_url is hidden (auto-filled)", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    expect(
      screen.queryByLabelText(/base[_ ]url|代理地址/i),
    ).not.toBeInTheDocument();
  });

  it("when custom chosen, base_url input shown", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getAllByTestId("preset-chip")[0]); // custom
    expect(
      screen.getByLabelText(/base[_ ]url|代理地址/i),
    ).toBeInTheDocument();
  });

  it("preset submit payload uses preset_id only", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    fireEvent.change(screen.getByLabelText(/anthropic[_ ]?api[_ ]?key|Anthropic API 密钥/i), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add|添加|confirm/i }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ preset_id: "deepseek", api_key: "sk-test" }),
    );
  });

  it("get-api-key link rendered when preset has api_key_url", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    const link = screen.getByRole("link", {
      name: /get[_ ]api[_ ]key|获取/i,
    });
    expect(link).toHaveAttribute("href", "https://platform.deepseek.com/api_keys");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("calls onClose when Escape pressed", () => {
    const onClose = vi.fn();
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("calls onClose when overlay clicked", () => {
    const onClose = vi.fn();
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /close-overlay/i }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("shows submit error when onSubmit rejects", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("boom"));
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    fireEvent.change(
      screen.getByLabelText(/anthropic[_ ]?api[_ ]?key|Anthropic API 密钥/i),
      { target: { value: "sk-test" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /add|添加|confirm/i }));
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });

  it("preserves user-edited displayName when switching preset", () => {
    render(
      <AddCredentialModal
        open
        presets={presetsWithSecond}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // 选第一个 preset，触发默认填充
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    const nameInput = screen.getByLabelText(
      /display[_ ]name|显示名/i,
    ) as HTMLInputElement;
    // 用户改名
    fireEvent.change(nameInput, { target: { value: "My Custom Name" } });
    expect(nameInput.value).toBe("My Custom Name");
    // 切换到另一个 preset
    fireEvent.click(screen.getByRole("button", { name: /Moonshot/i }));
    // 用户值应被保留
    expect(nameInput.value).toBe("My Custom Name");
  });
});
