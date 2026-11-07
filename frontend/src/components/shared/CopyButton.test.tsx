import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { CopyButton } from "./CopyButton";

// Clipboard is a repository boundary (browser API) missing in jsdom; install success/failure implementations as branches need;
// exercise the intermediate repository-owned @/utils/clipboard through its real implementation.
function stubClipboard(writeText: (text: string) => Promise<void>) {
  const spy = vi.fn(writeText);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: spy } });
  return spy;
}

afterEach(() => {
  Reflect.deleteProperty(navigator, "clipboard");
});

describe("CopyButton", () => {
  it("writes the text to the clipboard and switches to the copied state", async () => {
    const writeText = stubClipboard(() => Promise.resolve());
    render(<CopyButton text="sk-secret" />);

    fireEvent.click(screen.getByRole("button", { name: "复制消息" }));

    await screen.findByRole("button", { name: "已复制" });
    expect(writeText).toHaveBeenCalledWith("sk-secret");
  });

  it("uses the provided copied-state label", async () => {
    stubClipboard(() => Promise.resolve());
    render(<CopyButton text="sk-secret" label="复制密钥" copiedLabel="密钥已复制" />);

    fireEvent.click(screen.getByRole("button", { name: "复制密钥" }));

    expect(await screen.findByRole("button", { name: "密钥已复制" })).toBeInTheDocument();
  });

  it("returns to the idle state after the copied state expires", async () => {
    vi.useFakeTimers();
    stubClipboard(() => Promise.resolve());
    render(<CopyButton text="sk-secret" />);

    fireEvent.click(screen.getByRole("button", { name: "复制消息" }));
    await act(async () => {});
    expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1500));

    // Reset before giving Copied feedback again, otherwise second copies produce no visible change.
    expect(screen.getByRole("button", { name: "复制消息" })).toBeInTheDocument();
  });

  it("stays in the idle state when the clipboard write fails", async () => {
    const writeText = stubClipboard(() => Promise.reject(new Error("denied")));
    render(<CopyButton text="sk-secret" />);

    fireEvent.click(screen.getByRole("button", { name: "复制消息" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("sk-secret"));
    await act(async () => {});
    // Failed copies must not show Copied, which would imply clipboard contents exist.
    expect(screen.getByRole("button", { name: "复制消息" })).toBeInTheDocument();
  });
});
