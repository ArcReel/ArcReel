import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { createDeferred } from "@/test/deferred";
import type { CredentialSecretField, ProviderCredential } from "@/types";

import { CredentialList } from "./CredentialList";

const BASE_URL_LABEL = "接口地址（可选）";
const API_KEY_FIELDS: CredentialSecretField[] = [{ key: "api_key", label: "API Key" }];

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

function renderList({
  providerId = "dashscope",
  supportsBaseUrl = false,
  secretFields = API_KEY_FIELDS,
  secretFieldGroups = [secretFields.map((f) => f.key)],
  onChanged,
}: {
  providerId?: string;
  supportsBaseUrl?: boolean;
  secretFields?: CredentialSecretField[];
  secretFieldGroups?: string[][];
  onChanged?: () => void;
} = {}) {
  return render(
    <CredentialList
      providerId={providerId}
      supportsBaseUrl={supportsBaseUrl}
      secretFields={secretFields}
      secretFieldGroups={secretFieldGroups}
      onChanged={onChanged}
    />,
  );
}

async function openAddDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "添加密钥" }));
  return screen.findByRole("dialog", { name: "添加密钥" });
}

async function openEditDialog(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: `「${name}」的更多操作` }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "编辑" }));
  return screen.findByRole("dialog", { name: "编辑密钥" });
}

describe("pages/CredentialList", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the Base URL field in the add dialog only when the provider supports it", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    const { unmount } = renderList({ supportsBaseUrl: true });
    expect(within(await openAddDialog()).getByLabelText(BASE_URL_LABEL)).toBeInTheDocument();
    unmount();

    renderList({ providerId: "ark", supportsBaseUrl: false });
    const dialog = await openAddDialog();
    expect(within(dialog).getByLabelText("名称")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(BASE_URL_LABEL)).not.toBeInTheDocument();
  });

  it("switches the active key immediately and reports the change", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({
      credentials: [mockCred({ id: 1, name: "主账号", is_active: true }), mockCred({ id: 2, name: "备用账号" })],
    });
    const activateSpy = vi.spyOn(API, "activateCredential").mockResolvedValue();
    const onChanged = vi.fn();
    renderList({ onChanged });

    fireEvent.click(await screen.findByRole("button", { name: "激活 备用账号" }));

    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(activateSpy).toHaveBeenCalledWith("dashscope", 2);
  });

  it("deletes a key only after confirming in the alert dialog", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [mockCred()] });
    const deleteSpy = vi.spyOn(API, "deleteCredential").mockResolvedValue();
    const onChanged = vi.fn();
    renderList({ onChanged });

    const openConfirm = async () => {
      fireEvent.click(await screen.findByRole("button", { name: "「默认账号」的更多操作" }));
      fireEvent.click(await screen.findByRole("menuitem", { name: "删除" }));
      return screen.findByRole("alertdialog", { name: "删除密钥「默认账号」？" });
    };

    fireEvent.click(within(await openConfirm()).getByRole("button", { name: "取消" }));
    expect(deleteSpy).not.toHaveBeenCalled();

    fireEvent.click(within(await openConfirm()).getByRole("button", { name: "删除" }));
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(deleteSpy).toHaveBeenCalledWith("dashscope", 1);
  });

  it("keeps the dialog open with the error when adding a key fails", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    vi.spyOn(API, "createCredential").mockRejectedValue(new Error("密钥无效"));
    const onChanged = vi.fn();
    renderList({ onChanged });

    const dialog = await openAddDialog();
    fireEvent.change(within(dialog).getByLabelText("名称"), { target: { value: "主账号" } });
    fireEvent.change(within(dialog).getByLabelText("密钥"), { target: { value: "sk-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "添加密钥" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("密钥无效");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("ignores Esc while a key is being saved so a failure is still shown", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    const pending = createDeferred<never>();
    vi.spyOn(API, "createCredential").mockReturnValue(pending.promise);
    renderList();

    const dialog = await openAddDialog();
    fireEvent.change(within(dialog).getByLabelText("名称"), { target: { value: "主账号" } });
    fireEvent.change(within(dialog).getByLabelText("密钥"), { target: { value: "sk-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "添加密钥" }));
    fireEvent.keyDown(dialog, { key: "Escape" });

    await act(async () => pending.reject(new Error("密钥无效")));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("密钥无效");
  });
});

describe("pages/CredentialList two-secret (Kling)", () => {
  const KLING_SECRET_FIELDS = [
    { key: "access_key", label: "Access Key" },
    { key: "secret_key", label: "Secret Key" },
  ];
  const KLING_CRED = mockCred({
    id: 7,
    provider: "kling",
    name: "可灵账号",
    api_key_masked: null,
    access_key_masked: "AKfa…5678",
    secret_key_masked: "SKse…4321",
    is_active: true,
  });

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("submits both secrets on create, trimmed", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    const createSpy = vi.spyOn(API, "createCredential").mockResolvedValue({} as never);
    renderList({ providerId: "kling", secretFields: KLING_SECRET_FIELDS });

    const dialog = await openAddDialog();
    fireEvent.change(within(dialog).getByLabelText("名称"), { target: { value: "可灵账号" } });
    fireEvent.change(within(dialog).getByLabelText("Access Key"), { target: { value: "  AK-1\n" } });
    fireEvent.change(within(dialog).getByLabelText("Secret Key"), { target: { value: "\tSK-1 " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "添加密钥" }));

    await vi.waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(
        "kling",
        expect.objectContaining({ name: "可灵账号", access_key: "AK-1", secret_key: "SK-1" }),
      );
    });
  });

  it("does not overwrite a stored secret with a whitespace-only edit", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [KLING_CRED] });
    const updateSpy = vi.spyOn(API, "updateCredential").mockResolvedValue({} as never);
    renderList({ providerId: "kling", secretFields: KLING_SECRET_FIELDS });

    const dialog = await openEditDialog("可灵账号");
    fireEvent.change(within(dialog).getByLabelText("Secret Key"), { target: { value: "   " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    // 只含空白的输入经 trim 后为空，不应作为新值提交覆盖既有密钥
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it("shows each masked secret independently in the row", async () => {
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [KLING_CRED] });
    renderList({ providerId: "kling", secretFields: KLING_SECRET_FIELDS });

    expect(await screen.findByText(/AKfa…5678/)).toBeInTheDocument();
    expect(screen.getByText(/SKse…4321/)).toBeInTheDocument();
  });
});

describe("pages/CredentialList credential groups (api_key OR access_key+secret_key)", () => {
  const KLING_SECRET_FIELDS = [
    { key: "api_key", label: "API Key" },
    { key: "access_key", label: "Access Key" },
    { key: "secret_key", label: "Secret Key" },
  ];
  const KLING_SECRET_FIELD_GROUPS = [["api_key"], ["access_key", "secret_key"]];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
  });

  const renderKling = () =>
    renderList({ providerId: "kling", secretFields: KLING_SECRET_FIELDS, secretFieldGroups: KLING_SECRET_FIELD_GROUPS });

  it("describes the two groups and marks no single secret field as required", async () => {
    renderKling();
    const dialog = await openAddDialog();

    expect(within(dialog).getByText("密钥 或 Access Key + Secret Key")).toBeInTheDocument();
    for (const label of ["密钥", "Access Key", "Secret Key"]) {
      expect(within(dialog).getByLabelText(label)).not.toBeRequired();
    }
  });

  it.each([
    { filled: { "密钥": "sk-api-1" }, expected: { api_key: "sk-api-1" } },
    { filled: { "Access Key": "AK-1", "Secret Key": "SK-1" }, expected: { access_key: "AK-1", secret_key: "SK-1" } },
  ])("submits when one group is complete: $expected", async ({ filled, expected }) => {
    const createSpy = vi.spyOn(API, "createCredential").mockResolvedValue({} as never);
    renderKling();

    const dialog = await openAddDialog();
    fireEvent.change(within(dialog).getByLabelText("名称"), { target: { value: "可灵账号" } });
    for (const [label, value] of Object.entries(filled)) {
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
    }
    fireEvent.click(within(dialog).getByRole("button", { name: "添加密钥" }));

    await vi.waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith("kling", expect.objectContaining({ name: "可灵账号", ...expected }));
    });
  });

  it("rejects submit when no group is fully filled", async () => {
    const createSpy = vi.spyOn(API, "createCredential").mockResolvedValue({} as never);
    renderKling();

    const dialog = await openAddDialog();
    fireEvent.change(within(dialog).getByLabelText("名称"), { target: { value: "可灵账号" } });
    // 只填一半的双键组（access_key 无 secret_key），且未填 api_key —— 两组都不完整
    fireEvent.change(within(dialog).getByLabelText("Access Key"), { target: { value: "AK-1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "添加密钥" }));

    expect(await within(dialog).findByText("请至少完整填写一组鉴权字段")).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();
  });
});
