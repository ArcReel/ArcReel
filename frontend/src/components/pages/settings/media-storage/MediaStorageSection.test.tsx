import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { createDeferred } from "@/test/deferred";
import type { ObjectStorageTestResult } from "@/types";

import { MediaStorageSection } from "./MediaStorageSection";

const CONNECTION = {
  kind: "s3" as const,
  region: "cn-beijing",
  endpoint: "https://tos-s3-cn-beijing.volces.com",
  bucket: "old-relay",
  addressing_style: "virtual" as const,
  needs_endpoint_review: false,
  access_key_id_masked: "TEST…0001",
  secret_access_key_masked: "tos-…0001",
  updated_at: "2026-11-03T00:00:00Z",
};

const LOCAL_ONLY = {
  connection: null,
  new_image_target: "local" as const,
  pending_ingest_tasks: 0,
};

const ALL_PASSED_PROBE: ObjectStorageTestResult = {
  success: true,
  steps: [
    { step: "head_bucket", status: "passed" },
    { step: "put_object", status: "passed" },
    { step: "head_object", status: "passed" },
    { step: "presigned_get", status: "passed" },
    { step: "delete_object", status: "passed" },
  ],
  residual_object_key: null,
};

function renderGuarded() {
  const location = memoryLocation({ path: "/", record: true });
  render(
    <Router hook={location.hook}>
      <LeaveGuardProvider>
        <MediaStorageSection />
      </LeaveGuardProvider>
    </Router>,
  );
}

describe("MediaStorageSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "getObjectStorageConnection").mockResolvedValue(LOCAL_ONLY);
  });

  it("首次配置对象存储要求填写密钥，保存后只展示掩码；配置连接不改变新增图片保存位置", async () => {
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({
      kind: "s3",
      region: "cn-beijing",
      endpoint: "https://tos-s3-cn-beijing.volces.com",
      bucket: "arcreel-relay",
      addressing_style: "auto",
      needs_endpoint_review: false,
      access_key_id_masked: "TEST…0001",
      secret_access_key_masked: "tos-…0001",
      updated_at: "2026-11-03T00:00:00Z",
      cleanup_failures: {},
    });
    const user = userEvent.setup();
    render(<MediaStorageSection />);

    const targets = await screen.findByRole("radiogroup", { name: "新增图片保存位置" });
    expect(within(targets).getByRole("radio", { name: /^本地/ })).toBeChecked();
    expect(within(targets).getByRole("radio", { name: /^对象存储/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("配置对象存储连接后才能选择对象存储")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "配置对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "配置对象存储" });
    expect(within(dialog).getByText(/受管前缀独占提醒/)).toBeInTheDocument();
    expect(within(dialog).getByText(/arcreel-materials\//)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("区域"), "cn-beijing");
    await user.type(within(dialog).getByLabelText("Endpoint"), "https://tos-s3-cn-beijing.volces.com");
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
      kind: "s3",
      region: "cn-beijing",
      endpoint: "https://tos-s3-cn-beijing.volces.com",
      bucket: "arcreel-relay",
      addressing_style: "auto",
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

  it("提供 7 家主流厂商预设与自定义 S3 兼容，选择预设自动预填 Endpoint、寻址风格与填写提示，不向后端传厂商字段", async () => {
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({
      ...CONNECTION,
      region: "us-east-1",
      endpoint: "http://127.0.0.1:9000",
      bucket: "arcreel-local",
      addressing_style: "path",
      cleanup_failures: {},
    });
    const user = userEvent.setup();
    render(<MediaStorageSection />);

    await user.click(await screen.findByRole("button", { name: "配置对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "配置对象存储" });

    for (const name of [
      "阿里云 OSS",
      "腾讯云 COS",
      "火山引擎 TOS",
      "Cloudflare R2",
      "七牛云 Kodo",
      "AWS S3",
      "MinIO",
      "自定义 S3 兼容",
    ]) {
      expect(within(dialog).getByRole("button", { name })).toBeInTheDocument();
    }

    await user.click(within(dialog).getByRole("button", { name: "腾讯云 COS" }));
    expect(within(dialog).getByLabelText("区域")).toHaveValue("ap-guangzhou");
    expect(within(dialog).getByLabelText("Endpoint")).toHaveValue("https://cos.ap-guangzhou.myqcloud.com");
    expect(within(dialog).getByText(/BucketName-APPID/)).toBeInTheDocument();

    const regionInput = within(dialog).getByLabelText("区域");
    await user.clear(regionInput);
    await user.type(regionInput, "ap-beijing");
    expect(within(dialog).getByLabelText("Endpoint")).toHaveValue("https://cos.ap-beijing.myqcloud.com");

    await user.click(within(dialog).getByRole("button", { name: "七牛云 Kodo" }));
    expect(within(dialog).getByLabelText("区域")).toHaveValue("cn-east-1");
    expect(within(dialog).getByLabelText("Endpoint")).toHaveValue("https://s3.cn-east-1.qiniucs.com");
    expect(within(dialog).getByText(/七牛云空间名/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cloudflare R2" }));
    expect(within(dialog).getByLabelText("区域")).toHaveValue("auto");
    expect(within(dialog).getByLabelText("Endpoint")).toHaveValue("https://<account-id>.r2.cloudflarestorage.com");
    expect(within(dialog).getByText(/请将 Endpoint 中的占位符替换为实际地址后再保存/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("桶名"), "arcreel-local");
    await user.type(within(dialog).getByLabelText("Access Key ID"), "TEST-R2-AK");
    await user.type(within(dialog).getByLabelText("Secret Access Key"), "TEST-R2-SK");
    expect(within(dialog).getByRole("button", { name: "保存" })).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "MinIO" }));
    expect(within(dialog).getByLabelText("区域")).toHaveValue("us-east-1");
    expect(within(dialog).getByLabelText("Endpoint")).toHaveValue("http://127.0.0.1:9000");
    expect(within(dialog).getByRole("combobox", { name: "寻址风格" })).toHaveTextContent("路径风格");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(API.saveObjectStorageConnection).toHaveBeenCalledWith({
      kind: "s3",
      region: "us-east-1",
      endpoint: "http://127.0.0.1:9000",
      bucket: "arcreel-local",
      addressing_style: "path",
      access_key_id: "TEST-R2-AK",
      secret_access_key: "TEST-R2-SK",
      delete_remote_objects: undefined,
    });
  });

  it("选择新增图片保存位置立即保存；保存失败时保持原选择并说明原因", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    const saveTarget = vi
      .spyOn(API, "saveNewImageTarget")
      .mockResolvedValueOnce({ target: "object_storage" })
      .mockRejectedValueOnce(new Error("无法保存设置"));
    const user = userEvent.setup();
    render(<MediaStorageSection />);

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
    render(<MediaStorageSection />);
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
    renderSection: () => void = () => render(<MediaStorageSection />),
  ) {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({ ...CONNECTION, cleanup_failures: {} });
    renderSection();
    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    await user.type(within(dialog).getByLabelText("桶名"), "-2");
    expect(within(dialog).getByRole("button", { name: "保存" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Access Key ID"), "TEST-S3-AK-NEW");
    await user.type(within(dialog).getByLabelText("Secret Access Key"), "TEST-S3-SK-NEW");
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
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(await within(dialog).findByText(/有 2 个入库任务进行中/)).toBeInTheDocument();
  });

  it("删除时可放弃桶内文件，变更结果显示一次失败原因和数量", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    vi.spyOn(API, "deleteObjectStorageConnection").mockResolvedValue({ cleanup_failures: { denied: 2 } });
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const dialog = await screen.findByRole("alertdialog");
    const cleanup = within(dialog).getByRole("checkbox", { name: "同时删除 ArcReel 写入桶里的待删除文件" });
    expect(cleanup).toBeChecked();
    expect(within(dialog).getByText(/勾选后，无引用的图片/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/未勾选时/)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/不是 ArcReel 写入的对象不会被删除/)).toBeInTheDocument();
    await user.click(cleanup);
    expect(within(dialog).getByText(/未勾选时，桶里 ArcReel 写入的这些文件不会被删除/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("没有删除权限：2 个文件")).toBeInTheDocument();
    expect(API.deleteObjectStorageConnection).toHaveBeenCalledWith(false);
    expect(screen.queryByText(CONNECTION.bucket)).not.toBeInTheDocument();
  });

  it("连接测试在途时替换了连接，旧连接的测试结果不显示在新配置旁", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    const probe = createDeferred<ObjectStorageTestResult>();
    vi.spyOn(API, "checkObjectStorageConnection").mockReturnValue(probe.promise);
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({
      ...CONNECTION,
      bucket: "new-relay",
      cleanup_failures: {},
    });
    const user = userEvent.setup();
    render(<MediaStorageSection />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    const bucket = within(dialog).getByLabelText("桶名");
    await user.clear(bucket);
    await user.type(bucket, "new-relay");
    await user.type(within(dialog).getByLabelText("Access Key ID"), "TEST-S3-AK-NEW");
    await user.type(within(dialog).getByLabelText("Secret Access Key"), "TEST-S3-SK-NEW");
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

    await act(async () => probe.resolve(ALL_PASSED_PROBE));
    expect(screen.queryByText("ArcReel 实例侧存储测试通过")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测试连接" })).toBeEnabled();
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

  it("分级探针通过时区分实例侧测试通过与外部供应商可达，并消除自定义 TOS 域名核对提示", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY,
      connection: { ...CONNECTION, endpoint: "custom-tos.internal.example", needs_endpoint_review: true },
    });
    vi.spyOn(API, "checkObjectStorageConnection").mockResolvedValue(ALL_PASSED_PROBE);
    const user = userEvent.setup();
    render(<MediaStorageSection />);

    expect(await screen.findByText(/由自定义 TOS 域名升级而来/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("ArcReel 实例侧存储测试通过")).toBeInTheDocument();
    expect(screen.getByText(/外部模型或供应商能否从公网访问该 Endpoint/)).toBeInTheDocument();
    expect(screen.getByText("桶探测（HEAD Bucket）")).toBeInTheDocument();
    expect(screen.getByText("无登录态预签名下载与摘要校验（GET）")).toBeInTheDocument();
    expect(screen.getByText("临时探针清理（DELETE）")).toBeInTheDocument();
    expect(screen.queryByText(/由自定义 TOS 域名升级而来/)).not.toBeInTheDocument();
  });

  it("分级探针清理失败时展示残留探针 key、折叠技术详情，且不消除自定义域名核对提示", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY,
      connection: { ...CONNECTION, endpoint: "custom-tos.internal.example", needs_endpoint_review: true },
    });
    vi.spyOn(API, "checkObjectStorageConnection").mockResolvedValue({
      success: false,
      steps: [
        { step: "head_bucket", status: "passed" },
        { step: "put_object", status: "passed" },
        { step: "head_object", status: "passed" },
        { step: "presigned_get", status: "passed" },
        {
          step: "delete_object",
          status: "failed",
          error_kind: "denied",
          message: "访问凭证没有删除对象的权限。在对象存储控制台授予 DeleteObject 权限，并手动清理残留的临时探针对象",
          diagnostic: { code: "AccessDenied", status: 403, message: "delete denied" },
        },
      ],
      residual_object_key: "arcreel-relay/probes/abc123.bin",
    });
    const user = userEvent.setup();
    render(<MediaStorageSection />);

    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("ArcReel 实例侧存储测试未通过")).toBeInTheDocument();
    expect(screen.getByText("arcreel-relay/probes/abc123.bin")).toBeInTheDocument();
    expect(screen.getByText(/由自定义 TOS 域名升级而来/)).toBeInTheDocument();
    expect(screen.queryByText("AccessDenied: HTTP 403: delete denied")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText("AccessDenied: HTTP 403: delete denied")).toBeInTheDocument();
  });

  it("旧桶存在在用正式对象时，换桶先展示影响摘要弹窗、支持保留旧桶文件并完成收敛切换", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: { ...CONNECTION, in_use_object_count: 3 },
      new_image_target: "object_storage",
      pending_ingest_tasks: 2,
    });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValue({
      connection_id: 1,
      in_use_object_count: 3,
      in_use_reference_count: 5,
      download_object_count: 2,
      estimated_download_bytes: 2 * 1024 * 1024,
      pending_ingest_tasks: 2,
      in_flight_uploads: 0,
      relocating: false,
      relocate_delete_remote: null,
      relocate_mode: null,
      unrecoverable_items: [],
      progress: {
        running: false,
        phase: "idle",
        mode: null,
        delete_remote_objects: true,
        total_objects: 0,
        completed_objects: 0,
        downloaded_objects: 0,
        reused_local_objects: 0,
        total_references: 0,
        promoted_references: 0,
        conflicts_preserved: 0,
        failed_objects: 0,
        last_error_kind: null,
        last_error_message: null,
        unreachable: false,
      },
    });
    const runSpy = vi.spyOn(API, "runObjectStorageRelocation").mockResolvedValue({
      status: "completed",
      mode: "switch",
      delete_remote_objects: false,
      connection: { ...CONNECTION, bucket: "next-bucket", in_use_object_count: 0 },
      new_image_target: "object_storage",
      cleanup_failures: {},
      total_objects: 3,
      completed_objects: 3,
      downloaded_objects: 2,
      reused_local_objects: 1,
      total_references: 5,
      promoted_references: 5,
      conflicts_preserved: 1,
      failed_objects: 0,
      error_kind: null,
      error_message: null,
      error_detail: null,
      unreachable: false,
      unrecoverable_items: [],
    });

    const user = userEvent.setup();
    render(<MediaStorageSection />);

    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const editDialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    const bucketInput = within(editDialog).getByLabelText("桶名");
    await user.clear(bucketInput);
    await user.type(bucketInput, "next-bucket");
    await user.type(within(editDialog).getByLabelText("Access Key ID"), "NEW-AK");
    await user.type(within(editDialog).getByLabelText("Secret Access Key"), "NEW-SK");
    await user.click(within(editDialog).getByRole("button", { name: "保存" }));

    const relocDialog = await screen.findByRole("dialog", { name: "将旧桶在用图片拉回本地并切换连接" });
    expect(within(relocDialog).getByText("3 个对象（5 处引用）")).toBeInTheDocument();
    expect(within(relocDialog).getByText("2 个对象，预计 2.0 MB")).toBeInTheDocument();
    expect(within(relocDialog).getByText("1 个对象")).toBeInTheDocument();
    expect(within(relocDialog).getByText(/有 2 个入库任务进行中/)).toBeInTheDocument();

    const keepCheckbox = within(relocDialog).getByRole("checkbox", { name: "保留旧桶文件（不删除旧桶对象）" });
    expect(keepCheckbox).not.toBeChecked();
    await user.click(keepCheckbox);
    expect(keepCheckbox).toBeChecked();

    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: { ...CONNECTION, bucket: "next-bucket", in_use_object_count: 0 },
      new_image_target: "object_storage",
      pending_ingest_tasks: 0,
    });
    await user.click(within(relocDialog).getByRole("button", { name: "拉回本地并切换" }));

    expect(runSpy).toHaveBeenCalledWith({
      mode: "switch",
      delete_remote_objects: false,
      target: expect.objectContaining({
        bucket: "next-bucket",
        access_key_id: "NEW-AK",
        secret_access_key: "NEW-SK",
      }),
    });
    expect(await screen.findByText(/旧桶图片已全部收敛为永久本地文件（共 3 个对象、5 处引用）/)).toBeInTheDocument();
    expect(screen.getByText(/检测到 1 个本地当前文件被外部改写/)).toBeInTheDocument();
    expect(screen.getByText("next-bucket")).toBeInTheDocument();
  });

  it("旧桶失联导致收敛失败时支持重试、取消或进入显式二次确认强制断开并展示无法拉回清单", async () => {
    const missingItem = {
      holder_kind: "project_version",
      holder_label: "demo / characters/Bob (v1)",
      path: "versions/characters/Bob_v1.png",
      digest: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
      object_key: "arcreel-materials/sha256/ab/abcdef.png",
      size: 4096,
    };
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: { ...CONNECTION, in_use_object_count: 2 },
      new_image_target: "object_storage",
      pending_ingest_tasks: 0,
    });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValue({
      connection_id: 1,
      in_use_object_count: 2,
      in_use_reference_count: 2,
      download_object_count: 1,
      estimated_download_bytes: 4096,
      pending_ingest_tasks: 0,
      in_flight_uploads: 0,
      relocating: false,
      relocate_delete_remote: null,
      relocate_mode: null,
      unrecoverable_items: [missingItem],
      progress: {
        running: false,
        phase: "idle",
        mode: null,
        delete_remote_objects: true,
        total_objects: 0,
        completed_objects: 0,
        downloaded_objects: 0,
        reused_local_objects: 0,
        total_references: 0,
        promoted_references: 0,
        conflicts_preserved: 0,
        failed_objects: 0,
        last_error_kind: null,
        last_error_message: null,
        unreachable: false,
      },
    });
    vi.spyOn(API, "runObjectStorageRelocation").mockResolvedValue({
      status: "incomplete",
      mode: "delete",
      delete_remote_objects: true,
      connection: null,
      new_image_target: "object_storage",
      cleanup_failures: {},
      total_objects: 2,
      completed_objects: 1,
      downloaded_objects: 0,
      reused_local_objects: 1,
      total_references: 2,
      promoted_references: 1,
      conflicts_preserved: 0,
      failed_objects: 1,
      error_kind: "network",
      error_message: "无法连接对象存储。检查 Endpoint 与网络后重试",
      error_detail: "EndpointConnectionError",
      unreachable: true,
      unrecoverable_items: [missingItem],
    });
    const forceSpy = vi.spyOn(API, "forceDisconnectObjectStorage").mockResolvedValue({
      salvaged_objects: 1,
      salvaged_references: 2,
      conflicts_preserved: 0,
      unrecoverable_items: [missingItem],
      new_image_target: "local",
    });

    const user = userEvent.setup();
    render(<MediaStorageSection />);

    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const relocDialog = await screen.findByRole("dialog", { name: "将旧桶在用图片拉回本地并删除连接" });
    await user.click(within(relocDialog).getByRole("button", { name: "拉回本地并删除" }));

    expect(await within(relocDialog).findByText("收敛中途中断，可排除故障后继续重试")).toBeInTheDocument();
    expect(within(relocDialog).getByRole("button", { name: "继续重试" })).toBeInTheDocument();
    expect(within(relocDialog).getByRole("button", { name: "取消收敛" })).toBeInTheDocument();

    await user.click(within(relocDialog).getByRole("button", { name: "旧桶不可达？强制断开" }));
    const forceDialog = await screen.findByRole("alertdialog", { name: "旧桶永久不可达，强制断开连接？" });
    expect(within(forceDialog).getByText("demo / characters/Bob (v1)")).toBeInTheDocument();
    expect(within(forceDialog).getByText(/versions\/characters\/Bob_v1\.png/)).toBeInTheDocument();
    expect(within(forceDialog).getByRole("button", { name: "复制清单" })).toBeInTheDocument();

    vi.mocked(API.getObjectStorageConnection).mockResolvedValue(LOCAL_ONLY);
    await user.click(within(forceDialog).getByRole("button", { name: "确认强制断开" }));
    expect(forceSpy).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText(/已强制断开旧连接：抢救 1 个对象（2 处引用）为永久本地文件，1 项无法拉回/),
    ).toBeInTheDocument();
    expect(screen.getByText("还没有配置对象存储")).toBeInTheDocument();
  });

  it("页面重载时若连接仍处于收敛中，展示恢复横幅、允许取消收敛且禁止切回对象存储", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: {
        ...CONNECTION,
        relocating: true,
        relocate_delete_remote: false,
        relocate_mode: "delete",
        in_use_object_count: 1,
      },
      new_image_target: "local",
      pending_ingest_tasks: 0,
    });
    const cancelSpy = vi.spyOn(API, "cancelObjectStorageRelocation").mockResolvedValue({
      connection: { ...CONNECTION, relocating: false, relocate_delete_remote: null, relocate_mode: null, in_use_object_count: 1 },
      new_image_target: "local",
      pending_ingest_tasks: 0,
    });

    const user = userEvent.setup();
    render(<MediaStorageSection />);

    expect(await screen.findByText("旧桶图片收敛回本地尚未完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续收敛并删除" })).toBeInTheDocument();
    const targets = screen.getByRole("radiogroup", { name: "新增图片保存位置" });
    expect(within(targets).getByRole("radio", { name: /^对象存储/ })).toHaveAttribute("aria-disabled", "true");

    await user.click(screen.getByRole("button", { name: "取消收敛" }));
    expect(cancelSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText("旧桶图片收敛回本地尚未完成")).not.toBeInTheDocument());
  });
});
