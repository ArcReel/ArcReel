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
  pending_ingest_tasks: 0,
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
      cleanup_failures: {},
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
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY,
      connection: { ...CONNECTION, bucket: "arcreel-relay" },
    });
    await user.click(save);

    expect(API.saveObjectStorageConnection).toHaveBeenCalledWith({
      region: "cn-beijing",
      endpoint: "tos-cn-beijing.volces.com",
      bucket: "arcreel-relay",
      access_key_id: "TEST-TOS-AK-0001",
      secret_access_key: "tos-secret-0001",
      delete_remote_objects: undefined,
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

  it("改桶和删除时默认清理，提示未定论的入库任务；只改区域不提示", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY,
      connection: CONNECTION,
      pending_ingest_tasks: 3,
    });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);
    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("区域"), "-2");
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("桶名"), "-2");
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
    expect(within(dialog).getByText(/有 3 个入库任务进行中/)).toBeInTheDocument();
  });

  async function openBucketChange(
    user: ReturnType<typeof userEvent.setup>,
    renderSection: () => void = () => render(<AssetLibrarySection />),
  ) {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({ ...CONNECTION, cleanup_failures: {} });
    renderSection();
    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    await user.type(within(dialog).getByLabelText("桶名"), "-2");
    return dialog;
  }

  it("改桶默认清理时，保存先确认；取消不提交，焦点落在取消上", async () => {
    const user = userEvent.setup();
    const dialog = await openBucketChange(user);

    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    const confirm = await screen.findByRole("alertdialog", { name: "保存连接并删除旧桶里的文件？" });
    expect(API.saveObjectStorageConnection).not.toHaveBeenCalled();
    await waitFor(() => expect(within(confirm).getByRole("button", { name: "取消" })).toHaveFocus());

    await user.click(within(confirm).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(API.saveObjectStorageConnection).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "编辑对象存储" })).toBeInTheDocument();
  });

  it("在输入框按 Enter 提交改桶同样先确认，确认后才清理", async () => {
    const user = userEvent.setup();
    const dialog = await openBucketChange(user);

    await user.type(within(dialog).getByLabelText("桶名"), "{Enter}");
    const confirm = await screen.findByRole("alertdialog", { name: "保存连接并删除旧桶里的文件？" });
    expect(API.saveObjectStorageConnection).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "保存并删除" }));
    await waitFor(() =>
      expect(API.saveObjectStorageConnection).toHaveBeenCalledWith(
        expect.objectContaining({ bucket: "old-relay-2", delete_remote_objects: true }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑对象存储" })).not.toBeInTheDocument());
  });

  it("确认清理后保存失败，错误显示在编辑对话框，修改保留", async () => {
    const user = userEvent.setup();
    const dialog = await openBucketChange(user);
    vi.mocked(API.saveObjectStorageConnection).mockRejectedValueOnce(new Error("连接被占用"));

    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "保存并删除" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    const editor = screen.getByRole("dialog", { name: "编辑对象存储" });
    expect(await within(editor).findByRole("alert")).toHaveTextContent("连接被占用");
    expect(within(editor).getByLabelText("桶名")).toHaveValue("old-relay-2");
  });

  it("离开拦截的保存并离开遇到改桶清理时，先确认，不直接删除", async () => {
    const user = userEvent.setup();
    const dialog = await openBucketChange(user, renderGuarded);

    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "保存并离开" }));

    expect(await screen.findByRole("alertdialog", { name: "保存连接并删除旧桶里的文件？" })).toBeInTheDocument();
    expect(API.saveObjectStorageConnection).not.toHaveBeenCalled();
  });

  it("取消清理时，保存不再确认", async () => {
    const user = userEvent.setup();
    const dialog = await openBucketChange(user);
    await user.click(within(dialog).getByRole("checkbox"));

    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(API.saveObjectStorageConnection).toHaveBeenCalledWith(
        expect.objectContaining({ delete_remote_objects: false }),
      ),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("打开删除对话框时取最新的入库任务数", async () => {
    vi.mocked(API.getObjectStorageConnection)
      .mockResolvedValueOnce({ ...LOCAL_ONLY, connection: CONNECTION, pending_ingest_tasks: 0 })
      .mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION, pending_ingest_tasks: 2 });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(await within(dialog).findByText(/有 2 个入库任务进行中/)).toBeInTheDocument();
  });

  it("删除时可放弃桶内文件，变更结果显示一次失败原因和数量", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    vi.spyOn(API, "deleteObjectStorageConnection").mockResolvedValue({ cleanup_failures: { denied: 2 } });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const dialog = await screen.findByRole("alertdialog");
    const cleanup = within(dialog).getByRole("checkbox", { name: "同时删除 ArcReel 写入桶里的待删除文件" });
    expect(cleanup).toBeChecked();
    // 勾选与不勾选的说明各自准确，主说明不对 ArcReel 写入的文件作不删除承诺
    expect(within(dialog).getByText(/勾选后，无引用的图片/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/未勾选时/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/桶里已有的对象不会被删除/)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/不是 ArcReel 写入的对象不会被删除/)).toBeInTheDocument();
    await user.click(cleanup);
    expect(within(dialog).getByText(/未勾选时，桶里 ArcReel 写入的这些文件不会被删除/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/勾选后，无引用的图片/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("没有删除权限：2 个文件")).toBeInTheDocument();
    expect(API.deleteObjectStorageConnection).toHaveBeenCalledWith(false);
    expect(screen.queryByText(CONNECTION.bucket)).not.toBeInTheDocument();
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
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({
      ...CONNECTION,
      bucket: "new-relay",
      cleanup_failures: {},
    });
    const user = userEvent.setup();
    render(<AssetLibrarySection />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    const bucket = within(dialog).getByLabelText("桶名");
    await user.clear(bucket);
    await user.type(bucket, "new-relay");
    await user.click(within(dialog).getByRole("checkbox"));
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY,
      connection: { ...CONNECTION, bucket: "new-relay" },
    });
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(API.saveObjectStorageConnection).toHaveBeenCalledWith(
      expect.objectContaining({ delete_remote_objects: false }),
    );
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
