import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { ProviderCredential } from "@/types";

import { CredentialList } from "./CredentialList";

const BASE_URL_LABEL = "Base URL（可选）";

const mockCred = (overrides: Partial<ProviderCredential> = {}): ProviderCredential => ({
  id: 1,
  provider: "dashscope",
  name: "默认账号",
  api_key_masked: "sk-x…abcd",
  credentials_filename: null,
  base_url: null,
  is_active: false,
  created_at: "2026-06-01T00:00:00Z",
  ...overrides,
});

describe("pages/CredentialList base_url gating", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Base URL input in add form when provider supports it", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    render(<CredentialList providerId="dashscope" supportsBaseUrl />);

    fireEvent.click(await screen.findByRole("button", { name: /添加供应商/ }));

    expect(await screen.findByText(BASE_URL_LABEL)).toBeInTheDocument();
  });

  it("omits Base URL input in add form when provider does not support it", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    render(<CredentialList providerId="ark" supportsBaseUrl={false} />);

    fireEvent.click(await screen.findByRole("button", { name: /添加供应商/ }));

    // 表单已渲染（名称字段在），但不含 Base URL 输入
    expect(await screen.findByText("名称")).toBeInTheDocument();
    expect(screen.queryByText(BASE_URL_LABEL)).not.toBeInTheDocument();
  });

  it("renders Base URL input in edit form when provider supports it", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [mockCred()] });
    render(<CredentialList providerId="dashscope" supportsBaseUrl />);

    fireEvent.click(await screen.findByRole("button", { name: /编辑 默认账号/ }));

    expect(await screen.findByText(BASE_URL_LABEL)).toBeInTheDocument();
  });
});
