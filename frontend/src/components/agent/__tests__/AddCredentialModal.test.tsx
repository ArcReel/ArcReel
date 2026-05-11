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
    expect(chips[0]).toHaveTextContent(/custom|自定义|Tuỳ chỉnh/i);
  });

  it("when preset chosen, base_url is shown and prefilled with messages_url", () => {
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
    const baseUrlInput = screen.getByLabelText(
      /base[_ ]url|代理地址/i,
    ) as HTMLInputElement;
    expect(baseUrlInput).toBeInTheDocument();
    expect(baseUrlInput.value).toBe("https://api.deepseek.com/anthropic");
  });

  it("when custom chosen, base_url input shown and empty", () => {
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
    const input = screen.getByLabelText(
      /base[_ ]url|代理地址/i,
    ) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe("");
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
      expect.objectContaining({
        preset_id: "deepseek",
        api_key: "sk-test",
        base_url: "https://api.deepseek.com/anthropic",
      }),
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

  it("overwrites displayName when switching preset (even if user edited)", () => {
    render(
      <AddCredentialModal
        open
        presets={presetsWithSecond}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    const nameInput = screen.getByLabelText(
      /display[_ ]name|显示名/i,
    ) as HTMLInputElement;
    expect(nameInput.value).toBe("DeepSeek");
    // 用户改名
    fireEvent.change(nameInput, { target: { value: "My Custom Name" } });
    expect(nameInput.value).toBe("My Custom Name");
    // 切换到另一个 preset → displayName 跟随预设切换
    fireEvent.click(screen.getByRole("button", { name: /Moonshot/i }));
    expect(nameInput.value).toBe("Moonshot");
  });

  it("renders edit title when mode=edit", () => {
    render(
      <AddCredentialModal
        open
        mode="edit"
        presets={presets}
        customSentinelId="__custom__"
        initial={{
          preset_id: "deepseek",
          display_name: "DS Prod",
          base_url: "https://api.deepseek.com/anthropic",
          model: "deepseek-chat",
        }}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("heading", { name: /edit[_ ]credential|编辑凭证|Chỉnh sửa xác thực/i }),
    ).toBeInTheDocument();
  });

  it("does not require apiKey in edit mode and submits with empty key", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AddCredentialModal
        open
        mode="edit"
        presets={presets}
        customSentinelId="__custom__"
        initial={{
          preset_id: "deepseek",
          display_name: "DS Prod",
          base_url: "https://api.deepseek.com/anthropic",
          model: "deepseek-chat",
        }}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    // 不填 api_key，直接点提交按钮 (label 应为 Save / 保存 / Lưu)
    const submitBtn = screen.getByRole("button", {
      name: /^save$|^保存$|^Lưu$/i,
    });
    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled();
    });
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ api_key: "" }),
    );
  });

  it("disables preset chips in edit mode", () => {
    render(
      <AddCredentialModal
        open
        mode="edit"
        presets={presetsWithSecond}
        customSentinelId="__custom__"
        initial={{
          preset_id: "deepseek",
          display_name: "DS",
        }}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const chips = screen.getAllByTestId("preset-chip");
    for (const chip of chips) {
      expect(chip).toBeDisabled();
    }
  });
});
