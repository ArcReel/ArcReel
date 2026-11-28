import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { createDeferred } from "@/test/deferred";
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

function renderGuarded() {
  const location = memoryLocation({ path: "/", record: true });
  render(
    <Router hook={location.hook}>
      <LeaveGuardProvider>
        <AssetLibrarySection />
      </LeaveGuardProvider>
    </Router>,
  );
}

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
    vi.spyOn(API, "listAssetLibraryAccounts").mockResolvedValue(ACCOUNTS);
    vi.spyOn(API, "listAssetLibraryBindings").mockResolvedValue({ credentials: [STALE_BINDING] });
  });

  it("仅展示供应商素材库账号与推理凭证绑定，不包含对象存储配置", async () => {
    render(<AssetLibrarySection />);
    expect(await screen.findByRole("heading", { name: "供应商素材库", level: 2 })).toBeInTheDocument();
    expect(await screen.findByText("需要重新确认")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "对象存储" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "新增图片保存位置" })).not.toBeInTheDocument();
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

  it("一条绑定提交时其他行不能并发修改，完成后恢复", async () => {
    vi.mocked(API.listAssetLibraryBindings).mockResolvedValue({
      credentials: [STALE_BINDING, { ...STALE_BINDING, credential_id: 4, name: "Ark 备用 Key" }],
    });
    const confirmed = createDeferred<{ account_id: number; status: "confirmed" }>();
    vi.spyOn(API, "confirmAssetLibraryBinding").mockReturnValue(confirmed.promise);
    const user = userEvent.setup();
    render(<AssetLibrarySection />);
    const buttons = await screen.findAllByRole("button", { name: "重新确认" });
    await user.click(buttons[0]);
    expect(screen.getByRole("button", { name: "重新确认" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "「Ark 备用 Key」绑定的素材库账号" })).toBeDisabled();
    await act(async () => confirmed.resolve({ account_id: 7, status: "confirmed" }));
    expect(screen.getAllByRole("button", { name: "重新确认" })[1]).toBeEnabled();
  });

  it("账号对话框有未填完的修改时，关闭先经离开拦截询问", async () => {
    const user = userEvent.setup();
    renderGuarded();
    await user.click(await screen.findByRole("button", { name: "添加账号" }));
    const dialog = await screen.findByRole("dialog", { name: "添加账号" });
    await user.type(within(dialog).getByLabelText("名称"), "备用账号");

    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    const guard = await screen.findByRole("alertdialog");
    await user.click(within(guard).getByRole("button", { name: "继续编辑" }));
    expect(within(screen.getByRole("dialog", { name: "添加账号" })).getByLabelText("名称")).toHaveValue("备用账号");

    await user.click(within(screen.getByRole("dialog", { name: "添加账号" })).getByRole("button", { name: "取消" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "添加账号" })).not.toBeInTheDocument());
  });
});
