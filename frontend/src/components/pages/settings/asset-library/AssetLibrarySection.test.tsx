import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import type { AssetLibraryAccountList, AssetLibraryBindableCredential } from "@/types";

import { AssetLibrarySection } from "./AssetLibrarySection";

const ACCOUNTS: AssetLibraryAccountList = {
  sites: [
    { id: "volcengine", regions: ["cn-beijing"] },
    { id: "byteplus", regions: ["ap-southeast-1"] },
  ],
  default_project_name: "default",
  accounts: [
    {
      id: 7,
      site: "volcengine",
      name: "主账号",
      region: "cn-beijing",
      project_name: "film",
      access_key_id_masked: "TEST…acct",
      secret_access_key_masked: "ctrl…acct",
      created_at: "2026-11-01T00:00:00Z",
    },
  ],
};

const STALE_BINDING: AssetLibraryBindableCredential = {
  credential_type: "builtin",
  credential_id: 3,
  provider: "ark",
  name: "Ark 主 Key",
  api_key_masked: "ark-…cdef",
  endpoint: "https://ark.cn-beijing.volces.com/api/v3",
  binding: { account_id: 7, status: "needs_confirmation" },
};

describe("AssetLibrarySection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "getObjectStorageConnection").mockResolvedValue({ connection: null });
    vi.spyOn(API, "listAssetLibraryAccounts").mockResolvedValue(ACCOUNTS);
    vi.spyOn(API, "listAssetLibraryBindings").mockResolvedValue({ credentials: [STALE_BINDING] });
  });

  it("推理凭证变化后显示需要重新确认，重新确认按当前账号提交", async () => {
    vi.spyOn(API, "confirmAssetLibraryBinding").mockResolvedValue({ account_id: 7, status: "confirmed" });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);

    expect(await screen.findByText("需要重新确认")).toBeInTheDocument();
    vi.mocked(API.listAssetLibraryBindings).mockResolvedValue({
      credentials: [{ ...STALE_BINDING, binding: { account_id: 7, status: "confirmed" } }],
    });
    await user.click(screen.getByRole("button", { name: "重新确认" }));

    expect(API.confirmAssetLibraryBinding).toHaveBeenCalledWith("builtin", 3, 7);
    expect(await screen.findByText("已确认")).toBeInTheDocument();
  });

  it("首次配置对象存储要求填写密钥，保存后只展示掩码", async () => {
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({
      kind: "tos",
      region: "cn-beijing",
      endpoint: "tos-cn-beijing.volces.com",
      bucket: "arcreel-relay",
      access_key_id_masked: "TEST…0001",
      secret_access_key_masked: "tos-…0001",
      updated_at: "2026-11-03T00:00:00Z",
    });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);

    await user.click(await screen.findByRole("button", { name: "配置对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "配置对象存储" });
    await user.type(within(dialog).getByLabelText("区域"), "cn-beijing");
    await user.type(within(dialog).getByLabelText("Endpoint"), "tos-cn-beijing.volces.com");
    await user.type(within(dialog).getByLabelText("桶名"), "arcreel-relay");
    const save = within(dialog).getByRole("button", { name: "保存" });
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Access Key ID"), "TEST-TOS-AK-0001");
    await user.type(within(dialog).getByLabelText("Secret Access Key"), "tos-secret-0001");
    await user.click(save);

    expect(API.saveObjectStorageConnection).toHaveBeenCalledWith({
      region: "cn-beijing",
      endpoint: "tos-cn-beijing.volces.com",
      bucket: "arcreel-relay",
      access_key_id: "TEST-TOS-AK-0001",
      secret_access_key: "tos-secret-0001",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("arcreel-relay")).toBeInTheDocument();
    expect(screen.getByText("TEST…0001")).toBeInTheDocument();
    expect(screen.queryByText("tos-secret-0001")).not.toBeInTheDocument();
  });
});
