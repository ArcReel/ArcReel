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

const CONNECTION = {
  kind: "tos" as const,
  region: "cn-beijing",
  endpoint: "tos-cn-beijing.volces.com",
  bucket: "old-relay",
  access_key_id_masked: "TEST…0001",
  secret_access_key_masked: "tos-…0001",
  updated_at: "2026-11-03T00:00:00Z",
};

const LOCAL_ONLY = {
  connection: null,
  new_image_target: "local" as const,
  retained_relay_objects: 0,
  unreleased_image_objects: 0,
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
    vi.spyOn(API, "getObjectStorageConnection").mockResolvedValue(LOCAL_ONLY);
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

  it("首次配置对象存储要求填写密钥，保存后只展示掩码；配置连接不改变新增图片保存位置", async () => {
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

    const targets = await screen.findByRole("radiogroup", { name: "新增图片保存位置" });
    expect(within(targets).getByRole("radio", { name: /^本地/ })).toBeChecked();
    expect(within(targets).getByRole("radio", { name: /^对象存储/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("配置对象存储连接后才能选择对象存储")).toBeInTheDocument();

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
    expect(within(targets).getByRole("radio", { name: /^本地/ })).toBeChecked();
    expect(within(targets).getByRole("radio", { name: /^对象存储/ })).not.toHaveAttribute("aria-disabled", "true");
  });

  it("选择新增图片保存位置立即保存；保存失败时保持原选择并说明原因", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    const saveTarget = vi
      .spyOn(API, "saveNewImageTarget")
      .mockResolvedValueOnce({ target: "object_storage" })
      .mockRejectedValueOnce(new Error("无法保存设置"));
    const user = userEvent.setup();
    render(<AssetLibrarySection />);

    const targets = await screen.findByRole("radiogroup", { name: "新增图片保存位置" });
    await user.click(within(targets).getByRole("radio", { name: /^对象存储/ }));
    expect(saveTarget).toHaveBeenLastCalledWith("object_storage");
    await waitFor(() => expect(within(targets).getByRole("radio", { name: /^对象存储/ })).toBeChecked());

    await user.click(within(targets).getByRole("radio", { name: /^本地/ }));
    expect(saveTarget).toHaveBeenLastCalledWith("local");
    expect(await screen.findByText("无法保存设置")).toBeInTheDocument();
    expect(within(targets).getByRole("radio", { name: /^对象存储/ })).toBeChecked();
  });

  it("桶里还有未清理的中转副本与待删除图片对象时，编辑与删除连接前提示改动后需要手动清理", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY,
      connection: CONNECTION,
      retained_relay_objects: 3,
      unreleased_image_objects: 2,
    });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);

    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    expect(within(dialog).getByText(/桶里还有 3 个中转副本尚未清理/)).toBeInTheDocument();
    expect(within(dialog).getByText(/还有 2 个没有保存成功或不再使用的图片对象等待删除/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "取消" }));

    await user.click(screen.getByRole("button", { name: "删除对象存储连接" }));
    const confirm = await screen.findByRole("alertdialog");
    expect(within(confirm).getByText(/桶里还有 3 个中转副本尚未清理/)).toBeInTheDocument();
    expect(within(confirm).getByText(/还有 2 个没有保存成功或不再使用的图片对象等待删除/)).toBeInTheDocument();
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
  it("连接测试在途时替换了连接，旧连接的测试结果不显示在新配置旁", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    const probe = createDeferred<void>();
    vi.spyOn(API, "checkObjectStorageConnection").mockReturnValue(probe.promise as never);
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({ ...CONNECTION, bucket: "new-relay" });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    const bucket = within(dialog).getByLabelText("桶名");
    await user.clear(bucket);
    await user.type(bucket, "new-relay");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("new-relay")).toBeInTheDocument();

    await act(async () => probe.resolve());
    expect(screen.queryByText("连接正常，可以访问这个桶")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测试连接" })).toBeEnabled();
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

  it("对象存储对话框未修改时直接关闭，不询问", async () => {
    const user = userEvent.setup();
    renderGuarded();
    await user.click(await screen.findByRole("button", { name: "配置对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "配置对象存储" });
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
