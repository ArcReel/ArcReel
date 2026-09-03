import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { ApiKeyInfo } from "@/types";
import { ApiKeysTab } from "./ApiKeysTab";

const EXISTING_KEY: ApiKeyInfo = {
  id: 1,
  name: "外部智能体",
  key_prefix: "ak_live",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: null,
  last_used_at: null,
};

// 剪贴板是仓库边界（浏览器 API），jsdom 不实现；@/utils/clipboard 用真实实现走到这里。
function stubClipboard() {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  return writeText;
}

async function createKey(fullKey: string) {
  vi.mocked(API.createApiKey).mockResolvedValue({
    id: 2,
    name: "新密钥",
    key: fullKey,
    key_prefix: "ak_new",
    created_at: "2026-02-01T00:00:00Z",
    expires_at: null,
  });

  fireEvent.click(screen.getByRole("button", { name: "创建 API 密钥" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "名称" }), { target: { value: "新密钥" } });
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await screen.findByText("密钥已创建");
}

describe("ApiKeysTab", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "listApiKeys").mockResolvedValue([EXISTING_KEY]);
    vi.spyOn(API, "createApiKey").mockRejectedValue(new Error("unexpected create"));
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("shows only the masked prefix for keys that are already issued", async () => {
    render(<ApiKeysTab />);

    expect(await screen.findByText("ak_live****")).toBeInTheDocument();
    // 完整密钥只在创建那一次可见，列表接口本就不返回它
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("reveals the full key once after creation and copies it to the clipboard", async () => {
    const writeText = stubClipboard();
    render(<ApiKeysTab />);
    await screen.findByText("ak_live****");

    await createKey("ak_new_full_secret");

    // 只读输入框靠 aria-label 命名，密钥值本身不构成可访问名
    expect(screen.getByRole("textbox", { name: "API Key" })).toHaveValue("ak_new_full_secret");
    fireEvent.click(screen.getByRole("button", { name: "复制" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("ak_new_full_secret"));
  });

  it("announces the copied state on the copy button and resets it", async () => {
    stubClipboard();
    render(<ApiKeysTab />);
    await screen.findByText("ak_live****");
    await createKey("ak_new_full_secret");

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("button", { name: "复制" }));
      await act(async () => {});

      // 复制成功只换图标不换可访问名的话，读屏用户拿不到任何反馈
      expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1500));

      // 复位后第二次复制才能再次给出「已复制」反馈
      expect(screen.getByRole("button", { name: "复制" })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("hides the full key again once the creation dialog is closed", async () => {
    stubClipboard();
    render(<ApiKeysTab />);
    await screen.findByText("ak_live****");

    await createKey("ak_new_full_secret");
    fireEvent.click(screen.getByRole("button", { name: "完成" }));

    // 关闭后新密钥只以前缀形态留在列表里，完整值不再出现在任何位置
    expect(await screen.findByText("ak_new****")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("ak_new_full_secret")).not.toBeInTheDocument();
  });
});
