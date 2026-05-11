import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentCredential } from "@/types/agent-credential";

import { CredentialList } from "../CredentialList";

const mockCred = (overrides: Partial<AgentCredential> = {}): AgentCredential => ({
  id: 1,
  preset_id: "deepseek",
  display_name: "DeepSeek",
  icon_key: "DeepSeek",
  base_url: "https://api.deepseek.com/anthropic",
  api_key_masked: "sk-x…abcd",
  model: "deepseek-chat",
  haiku_model: null,
  sonnet_model: null,
  opus_model: null,
  subagent_model: null,
  is_active: false,
  created_at: "2026-05-11T00:00:00Z",
  ...overrides,
});

describe("CredentialList", () => {
  it("calls onActivate when activate clicked", () => {
    const onActivate = vi.fn();
    render(
      <CredentialList
        credentials={[mockCred()]}
        onActivate={onActivate}
        onTest={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /set active|activate/i }));
    expect(onActivate).toHaveBeenCalledWith(1);
  });

  it("disables delete on active credential", () => {
    render(
      <CredentialList
        credentials={[mockCred({ is_active: true })]}
        onActivate={vi.fn()}
        onTest={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const deleteBtn = screen.getByRole("button", { name: /delete|remove/i });
    expect(deleteBtn).toBeDisabled();
  });

  it("renders empty hint when no credentials", () => {
    render(
      <CredentialList
        credentials={[]}
        onActivate={vi.fn()}
        onTest={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByTestId("credential-list-empty")).toBeInTheDocument();
  });
});
