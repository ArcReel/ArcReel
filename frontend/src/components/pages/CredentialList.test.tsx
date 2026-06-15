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

describe("pages/CredentialList two-secret (Kling)", () => {
  const KLING_SECRET_FIELDS = [
    { key: "access_key", label: "Access Key" },
    { key: "secret_key", label: "Secret Key" },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders two secret inputs in the add form by required_keys", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    render(
      <CredentialList providerId="kling" supportsBaseUrl secretFields={KLING_SECRET_FIELDS} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /添加供应商/ }));

    expect(await screen.findByLabelText(/Access Key/)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Secret Key/)).toBeInTheDocument();
  });

  it("submits both secrets on create", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    const createSpy = vi
      .spyOn(API, "createCredential")
      .mockResolvedValue({} as never);
    render(
      <CredentialList providerId="kling" supportsBaseUrl={false} secretFields={KLING_SECRET_FIELDS} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /添加供应商/ }));
    fireEvent.change(await screen.findByLabelText(/名称/), { target: { value: "可灵账号" } });
    fireEvent.change(await screen.findByLabelText(/Access Key/), { target: { value: "AK-1" } });
    fireEvent.change(await screen.findByLabelText(/Secret Key/), { target: { value: "SK-1" } });
    fireEvent.click(screen.getByRole("button", { name: /添加$/ }));

    await vi.waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith("kling", expect.objectContaining({
        name: "可灵账号",
        access_key: "AK-1",
        secret_key: "SK-1",
      }));
    });
  });

  it("trims surrounding whitespace from secrets on create", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    const createSpy = vi
      .spyOn(API, "createCredential")
      .mockResolvedValue({} as never);
    render(
      <CredentialList providerId="kling" supportsBaseUrl={false} secretFields={KLING_SECRET_FIELDS} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /添加供应商/ }));
    fireEvent.change(await screen.findByLabelText(/名称/), { target: { value: "可灵账号" } });
    fireEvent.change(await screen.findByLabelText(/Access Key/), { target: { value: "  AK-1\n" } });
    fireEvent.change(await screen.findByLabelText(/Secret Key/), { target: { value: "\tSK-1 " } });
    fireEvent.click(screen.getByRole("button", { name: /添加$/ }));

    await vi.waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith("kling", expect.objectContaining({
        access_key: "AK-1",
        secret_key: "SK-1",
      }));
    });
  });

  it("does not overwrite a stored secret with a whitespace-only edit", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({
      credentials: [
        {
          id: 7,
          provider: "kling",
          name: "可灵账号",
          api_key_masked: null,
          credentials_filename: null,
          base_url: null,
          access_key_masked: "AKfa…5678",
          secret_key_masked: "SKse…4321",
          is_active: true,
          created_at: "2026-06-01T00:00:00Z",
        },
      ],
    });
    const updateSpy = vi.spyOn(API, "updateCredential").mockResolvedValue({} as never);
    render(
      <CredentialList providerId="kling" supportsBaseUrl={false} secretFields={KLING_SECRET_FIELDS} />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /编辑 可灵账号/ }));
    fireEvent.change(await screen.findByLabelText(/Secret Key/), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    // 空白-only 输入经 trim 后为空，不应作为新值提交覆盖既有密钥
    await vi.waitFor(() => {
      expect(updateSpy).not.toHaveBeenCalled();
    });
  });

  it("shows each masked secret independently in the row", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({
      credentials: [
        {
          id: 7,
          provider: "kling",
          name: "可灵账号",
          api_key_masked: null,
          credentials_filename: null,
          base_url: null,
          access_key_masked: "AKfa…5678",
          secret_key_masked: "SKse…4321",
          is_active: true,
          created_at: "2026-06-01T00:00:00Z",
        },
      ],
    });
    render(
      <CredentialList providerId="kling" supportsBaseUrl={false} secretFields={KLING_SECRET_FIELDS} />,
    );

    expect(await screen.findByText(/AKfa…5678/)).toBeInTheDocument();
    expect(await screen.findByText(/SKse…4321/)).toBeInTheDocument();
  });
});
