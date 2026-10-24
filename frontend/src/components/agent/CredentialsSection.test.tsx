import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { PresetProvider } from "@/types/agent-credential";

import { CredentialsSection } from "./CredentialsSection";

const preset: PresetProvider = {
  id: "deepseek",
  display_name: "DeepSeek",
  icon_key: "DeepSeek",
  messages_url: "https://api.deepseek.com/anthropic",
  discovery_url: "https://api.deepseek.com",
  default_model: "deepseek-chat",
  suggested_models: ["deepseek-chat"],
  docs_url: null,
  api_key_url: null,
  notes: null,
  api_key_pattern: null,
  is_recommended: true,
};

beforeEach(() => {
  vi.spyOn(API, "listAgentCredentials").mockResolvedValue({ credentials: [] });
  vi.spyOn(API, "listAgentPresetProviders").mockResolvedValue({
    providers: [preset],
    custom_sentinel_id: "__custom__",
  });
  vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
});

describe("CredentialsSection", () => {
  it("reopens the add dialog without the previous draft or test result", async () => {
    vi.spyOn(API, "testAgentConnectionDraft").mockResolvedValue({
      overall: "ok",
      messages_probe: { success: true, status_code: 200, latency_ms: 123, error: null },
      diagnosis: null,
      suggestion: null,
      messages_url: "https://api.deepseek.com/anthropic/v1/messages",
    });
    render(<CredentialsSection />);
    await waitFor(() => expect(API.listAgentPresetProviders).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "添加供应商" }));
    fireEvent.click(await screen.findByRole("button", { name: /DeepSeek/i }));
    fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByTestId("test-connection"));
    await screen.findByText(/连通正常/);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "添加供应商" }));
    await screen.findByRole("dialog");
    expect(screen.queryByText(/连通正常/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("密钥")).toHaveValue("");
  });
});
