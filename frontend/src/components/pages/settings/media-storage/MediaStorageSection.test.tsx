import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API } from "@/api";
import { ApiRequestError } from "@/api/errors";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { createDeferred } from "@/test/deferred";
import type { ObjectStorageTestResult } from "@/types";

import { detectPresetForEndpoint } from "./object-storage-presets";
import { MediaStorageSection } from "./MediaStorageSection";

const CONNECTION = {
  id: 1,
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
      id: 1,
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

    await user.click(within(dialog).getByRole("button", { name: "AWS S3" }));
    await user.clear(regionInput);
    await user.type(regionInput, "cn-north-1");
    expect(within(dialog).getByLabelText("Endpoint")).toHaveValue("https://s3.cn-north-1.amazonaws.com.cn");

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
    expect(within(dialog).getByLabelText("Access Key ID")).toHaveValue("");
    expect(within(dialog).getByLabelText("Secret Access Key")).toHaveValue("");
    await user.type(within(dialog).getByLabelText("Access Key ID"), "TEST-MINIO-AK");
    await user.type(within(dialog).getByLabelText("Secret Access Key"), "TEST-MINIO-SK");
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
      access_key_id: "TEST-MINIO-AK",
      secret_access_key: "TEST-MINIO-SK",
      delete_remote_objects: undefined,
    });
  });

  it("选择新增图片保存位置立即保存；保存失败时保持原选择并说明原因", async () => {
    vi.mocked(API.getObjectStorageConnection)
      .mockResolvedValueOnce({ ...LOCAL_ONLY, connection: CONNECTION })
      .mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION, new_image_target: "object_storage" });
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
    const replacement = { ...CONNECTION, id: 2, bucket: "old-relay-2" };
    vi.spyOn(API, "saveObjectStorageConnection").mockImplementation(async () => {
      vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: replacement });
      return { ...replacement, cleanup_failures: {} };
    });
    renderSection();
    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑对象存储" });
    await user.type(within(dialog).getByLabelText("桶名"), "-2");
    expect(within(dialog).getByRole("button", { name: "保存" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Access Key ID"), "TEST-S3-AK-NEW");
    await user.type(within(dialog).getByLabelText("Secret Access Key"), "TEST-S3-SK-NEW");
    return dialog;
  }

  it("保存冲突后保留编辑内容，并按最新在用对象数进入收敛", async () => {
    const user = userEvent.setup();
    const editor = await openBucketChange(user);
    vi.mocked(API.saveObjectStorageConnection).mockRejectedValueOnce(new ApiRequestError("连接已被引用", undefined, 409));
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY, connection: { ...CONNECTION, in_use_object_count: 1 },
    });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValue(relocationPlan);
    await user.click(within(editor).getByRole("checkbox"));
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    expect(await within(editor).findByText("连接已被引用")).toBeInTheDocument();
    expect(within(editor).getByLabelText("桶名")).toHaveValue("old-relay-2");
    expect(within(editor).getByLabelText("Secret Access Key")).toHaveValue("TEST-S3-SK-NEW");
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alertdialog", { name: "将旧桶在用图片拉回本地并切换连接" })).toBeInTheDocument();
    expect(API.saveObjectStorageConnection).toHaveBeenCalledTimes(1);
  });

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
        expect.objectContaining({ bucket: "old-relay-2", delete_remote_objects: true, expected_connection_id: 1 }),
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

  it("按 Endpoint 主机识别预设，忽略路径和相似域名", () => {
    expect(detectPresetForEndpoint("https://example.com/oss.aliyuncs.com")).toBe("custom");
    expect(detectPresetForEndpoint("https://notaliyuncs.com")).toBe("custom");
    expect(detectPresetForEndpoint("oss.aliyuncs.com/bucket")).toBe("aliyun-oss");
  });

  it("保存期间锁定连接配置，失败后恢复编辑", async () => {
    const user = userEvent.setup();
    const dialog = await openBucketChange(user);
    const saving = createDeferred<Awaited<ReturnType<typeof API.saveObjectStorageConnection>>>();
    vi.mocked(API.saveObjectStorageConnection).mockReturnValue(saving.promise);
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    const fields = ["区域", "Endpoint", "桶名", "Access Key ID", "Secret Access Key"]
      .map((label) => within(dialog).getByLabelText(label));
    const presets = within(within(dialog).getByRole("group")).getAllByRole("button");
    for (const control of [...fields, ...presets]) expect(control).toBeDisabled();
    expect(within(dialog).getByRole("combobox")).toBeDisabled();
    await act(async () => saving.reject(new Error("连接被占用")));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("连接被占用");
    for (const control of [...fields, ...presets]) expect(control).toBeEnabled();
    expect(within(dialog).getByRole("combobox")).toBeEnabled();
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

  it("删除时可放弃桶内文件，无清理失败提示", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    vi.spyOn(API, "deleteObjectStorageConnection").mockResolvedValue({ cleanup_failures: {} });
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
    expect(screen.queryByText("没有删除权限：2 个文件")).not.toBeInTheDocument();
    expect(API.deleteObjectStorageConnection).toHaveBeenCalledWith(false, { expectedConnectionId: CONNECTION.id, resetNewImageTarget: false });
    expect(screen.queryByText(CONNECTION.bucket)).not.toBeInTheDocument();
  });

  it("保存后的连接读取迟到时保留已删除的空态", async () => {
    const stale = createDeferred<Awaited<ReturnType<typeof API.getObjectStorageConnection>>>();
    const settings = { ...LOCAL_ONLY, connection: CONNECTION };
    vi.mocked(API.getObjectStorageConnection)
      .mockResolvedValueOnce(settings).mockResolvedValueOnce(settings)
      .mockReturnValueOnce(stale.promise).mockResolvedValue(settings);
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({ ...CONNECTION, cleanup_failures: {} });
    vi.spyOn(API, "deleteObjectStorageConnection").mockResolvedValue({ cleanup_failures: {} });
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    const editor = await screen.findByRole("dialog");
    await user.type(within(editor).getByLabelText("区域"), "-2");
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "删除" }));
    await act(async () => stale.resolve(settings));
    expect(screen.getByText("还没有配置对象存储")).toBeInTheDocument();
  });

  it("保留默认清理选项时显示旧桶删除失败的文件数", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    vi.spyOn(API, "deleteObjectStorageConnection").mockResolvedValue({ cleanup_failures: { denied: 2 } });
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "删除" }));
    expect(await screen.findByText("没有删除权限：2 个文件")).toBeInTheDocument();
  });

  it.each([1, 2])("探针补拉先返回时按最新连接 ID %s 复核删除", async (id) => {
    const settings = { ...LOCAL_ONLY, connection: { ...CONNECTION, needs_endpoint_review: true } };
    const clicked = createDeferred<typeof settings>();
    vi.mocked(API.getObjectStorageConnection).mockResolvedValueOnce(settings).mockReturnValueOnce(clicked.promise)
      .mockResolvedValue({ ...settings, connection: { ...CONNECTION, id, needs_endpoint_review: false } });
    const probe = createDeferred<ObjectStorageTestResult>();
    vi.spyOn(API, "checkObjectStorageConnection").mockReturnValue(probe.promise);
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "删除对象存储连接" }));
    await act(async () => probe.resolve(ALL_PASSED_PROBE));
    await act(async () => clicked.resolve(settings));
    expect(Boolean(screen.queryByRole("alertdialog"))).toBe(id === 1);
  });

  it("打开编辑器前等待最新寻址风格，保存冲突后仍提交原连接 ID", async () => {
    const latest = createDeferred<Awaited<ReturnType<typeof API.getObjectStorageConnection>>>();
    vi.mocked(API.getObjectStorageConnection).mockResolvedValueOnce({ ...LOCAL_ONLY, connection: CONNECTION })
      .mockReturnValueOnce(latest.promise).mockResolvedValue({ ...LOCAL_ONLY, connection: { ...CONNECTION, id: 2 } });
    const save = vi.spyOn(API, "saveObjectStorageConnection").mockRejectedValue(new ApiRequestError("连接已变更", undefined, 409));
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "编辑对象存储" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => latest.resolve({ ...LOCAL_ONLY, connection: { ...CONNECTION, addressing_style: "path" } }));
    const editor = within(await screen.findByRole("dialog"));
    await user.type(editor.getByLabelText("区域"), "-2");
    await user.click(editor.getByRole("button", { name: "保存" }));
    expect(await editor.findByText("连接已变更")).toBeInTheDocument();
    await user.click(editor.getByRole("button", { name: "保存" }));
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ expected_connection_id: 1, addressing_style: "path", region: "cn-beijing-2" }));
  });

  it("连接测试在途时替换了连接，旧连接的测试结果不显示在新配置旁", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({ ...LOCAL_ONLY, connection: CONNECTION });
    const probe = createDeferred<ObjectStorageTestResult>();
    vi.spyOn(API, "checkObjectStorageConnection").mockReturnValue(probe.promise);
    vi.spyOn(API, "saveObjectStorageConnection").mockResolvedValue({
      ...CONNECTION,
      id: 2,
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
      connection: { ...CONNECTION, id: 2, bucket: "new-relay" },
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

  it("保存目标后重读连接，轮换密钥时丢弃旧探针结果", async () => {
    const settings = { ...LOCAL_ONLY, connection: CONNECTION };
    vi.mocked(API.getObjectStorageConnection).mockResolvedValueOnce(settings).mockResolvedValue({
      ...settings, new_image_target: "object_storage",
      connection: { ...CONNECTION, updated_at: "2026-11-04T00:00:00Z" },
    });
    vi.spyOn(API, "saveNewImageTarget").mockResolvedValue({ target: "object_storage" });
    const probe = createDeferred<ObjectStorageTestResult>();
    vi.spyOn(API, "checkObjectStorageConnection").mockReturnValue(probe.promise);
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("radio", { name: /^对象存储/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "测试连接" })).toBeEnabled());
    await act(async () => probe.resolve(ALL_PASSED_PROBE));
    expect(screen.queryByText("ArcReel 实例侧存储测试通过")).not.toBeInTheDocument();
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

  it.each([true, false])("分级探针通过后按服务端状态显示自定义域名核对提示（%s）", async (needsReview) => {
    const connection = { ...CONNECTION, endpoint: "custom-tos.internal.example" };
    vi.mocked(API.getObjectStorageConnection)
      .mockResolvedValueOnce({ ...LOCAL_ONLY, connection: { ...connection, needs_endpoint_review: true } })
      .mockResolvedValue({ ...LOCAL_ONLY, connection: { ...connection, needs_endpoint_review: needsReview } });
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
    expect(API.getObjectStorageConnection).toHaveBeenCalledTimes(2);
    expect(Boolean(screen.queryByText(/由自定义 TOS 域名升级而来/))).toBe(needsReview);
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
    vi.mocked(API.getObjectStorageConnection).mockResolvedValueOnce({ ...LOCAL_ONLY, connection: CONNECTION }).mockResolvedValue({
      connection: { ...CONNECTION, in_use_object_count: 4 },
      new_image_target: "object_storage",
      pending_ingest_tasks: 2,
    });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValue({
      connection_id: 1,
      in_use_object_count: 4,
      in_use_reference_count: 5,
      reusable_local_object_count: 1,
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
      connection: { ...CONNECTION, id: 2, bucket: "next-bucket", in_use_object_count: 0 },
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
    await user.type(within(editDialog).getByLabelText("Endpoint"), "/");
    expect(within(editDialog).getByRole("button", { name: "保存" })).toBeEnabled();
    expect(within(editDialog).queryByRole("checkbox")).not.toBeInTheDocument();
    const bucketInput = within(editDialog).getByLabelText("桶名");
    await user.clear(bucketInput);
    await user.type(bucketInput, "next-bucket");
    await user.type(within(editDialog).getByLabelText("Access Key ID"), "NEW-AK");
    await user.type(within(editDialog).getByLabelText("Secret Access Key"), "NEW-SK");
    await user.click(within(editDialog).getByRole("button", { name: "保存" }));

    const preview = await screen.findByRole("alertdialog");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const reads = vi.mocked(API.getObjectStorageConnection).mock.calls.length;
    await user.click(within(preview).getByRole("button", { name: "取消" }));
    const restored = await screen.findByRole("dialog", { name: "编辑对象存储" });
    expect(within(restored).getByLabelText("桶名")).toHaveValue("next-bucket");
    expect(within(restored).getByLabelText("Access Key ID")).toHaveValue("NEW-AK");
    expect(within(restored).getByLabelText("Secret Access Key")).toHaveValue("NEW-SK");
    expect(API.getObjectStorageConnection).toHaveBeenCalledTimes(reads);
    await user.click(within(restored).getByRole("button", { name: "保存" }));

    const relocDialog = await screen.findByRole("alertdialog", { name: "将旧桶在用图片拉回本地并切换连接" });
    expect(within(relocDialog).getByText("4 个对象（5 处引用）")).toBeInTheDocument();
    expect(within(relocDialog).getByText("2 个对象，预计 2.0 MB")).toBeInTheDocument();
    expect(within(relocDialog).getByText("1 个对象")).toBeInTheDocument();
    expect(within(relocDialog).getByText(/有 2 个入库任务进行中/)).toBeInTheDocument();

    const keepCheckbox = within(relocDialog).getByRole("checkbox", { name: "保留旧桶文件（不删除旧桶对象）" });
    expect(keepCheckbox).not.toBeChecked();
    await user.click(keepCheckbox);
    expect(keepCheckbox).toBeChecked();

    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: { ...CONNECTION, id: 2, bucket: "next-bucket", in_use_object_count: 0 },
      new_image_target: "object_storage",
      pending_ingest_tasks: 0,
    });
    await user.click(within(relocDialog).getByRole("button", { name: "拉回本地并切换" }));

    expect(runSpy).toHaveBeenCalledWith({
      expected_connection_id: 1,
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

  const relocationPlan = {
    connection_id: 1,
    in_use_object_count: 2,
    in_use_reference_count: 2,
    reusable_local_object_count: 1,
    download_object_count: 1,
    estimated_download_bytes: 4096,
    pending_ingest_tasks: 0,
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
  } satisfies Awaited<ReturnType<typeof API.getObjectStorageRelocationPlan>>;

  async function openInterruptedRelocation(nextConnectionId = 1, refreshFails = false) {
    const missingItem = {
      confirmation_key: "deleted-version-confirmation",
      holder_kind: "deleted_project_version",
      holder_label: "demo / characters/Bob (v1)",
      path: "versions/characters/Bob_v1.png",
      digest: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
      object_key: "arcreel-materials/sha256/ab/abcdef.png",
      size: 4096,
    };
    const globalItem = { ...missingItem, confirmation_key: "global-confirmation", holder_kind: "global_asset", holder_label: "Bob", path: "global/Bob.png" };
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: { ...CONNECTION, in_use_object_count: 2 },
      new_image_target: "object_storage",
      pending_ingest_tasks: 0,
    });
    const plan = { ...relocationPlan, unrecoverable_items: [missingItem, globalItem] };
    const getPlan = vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValueOnce(plan);
    if (refreshFails) getPlan.mockRejectedValueOnce(new Error("读取计划失败"));
    getPlan.mockResolvedValue({ ...plan, connection_id: nextConnectionId });
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
      unrecoverable_items: [globalItem],
      new_image_target: "local",
    });

    const user = userEvent.setup();
    render(<MediaStorageSection />);

    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const relocDialog = await screen.findByRole("alertdialog", { name: "将旧桶在用图片拉回本地并删除连接" });
    await user.click(within(relocDialog).getByRole("button", { name: "拉回本地并删除" }));

    expect(await within(relocDialog).findByText("收敛中途中断，可排除故障后继续重试")).toBeInTheDocument();
    expect(within(relocDialog).getByRole("button", { name: "继续重试" })).toBeInTheDocument();
    expect(within(relocDialog).getByRole("button", { name: "取消收敛" })).toBeInTheDocument();

    return { user, relocDialog, missingItem, globalItem, plan, forceSpy };
  }

  it("首次读取计划发现连接已切换时阻止执行", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      ...LOCAL_ONLY, connection: { ...CONNECTION, in_use_object_count: 1 },
    });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValue({
      ...relocationPlan, connection_id: CONNECTION.id + 1,
    });
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(await within(dialog).findByText(/对象存储连接已变更/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "拉回本地并删除" })).toBeDisabled();
  });

  it("运行响应丢失后读取锁定状态，重试时清除上一轮进度", async () => {
    vi.mocked(API.getObjectStorageConnection)
      .mockResolvedValueOnce({ ...LOCAL_ONLY, connection: CONNECTION })
      .mockResolvedValue({ ...LOCAL_ONLY, connection: { ...CONNECTION, in_use_object_count: 2 } });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockResolvedValueOnce(relocationPlan).mockResolvedValue({
      ...relocationPlan, relocating: true, relocate_delete_remote: true,
    });
    const first = createDeferred<Awaited<ReturnType<typeof API.runObjectStorageRelocation>>>();
    const retry = createDeferred<Awaited<ReturnType<typeof API.runObjectStorageRelocation>>>();
    vi.spyOn(API, "runObjectStorageRelocation").mockReturnValueOnce(first.promise).mockReturnValueOnce(retry.promise);
    vi.spyOn(API, "getObjectStorageRelocationStatus").mockResolvedValueOnce({
      ...relocationPlan.progress, total_objects: 2, completed_objects: 1,
    }).mockResolvedValue(relocationPlan.progress);
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "删除对象存储连接" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    const start = await dialog.findByRole("button", { name: "拉回本地并删除" });
    await user.click(start);
    expect(await dialog.findByText(/已完成 1 \/ 2 个对象/)).toBeInTheDocument();
    await act(async () => first.reject(new Error("响应丢失")));
    expect(await dialog.findByRole("button", { name: "取消收敛" })).toBeEnabled();
    expect(dialog.getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
    await user.click(start);
    expect(start).toBeDisabled();
    expect(dialog.queryByText(/已完成 1 \/ 2 个对象/)).not.toBeInTheDocument();
    await act(async () => retry.reject(new Error("响应丢失")));
  });

  it("计划刷新发现新连接后禁用旧弹窗操作", async () => {
    const { relocDialog } = await openInterruptedRelocation(2);
    expect(await within(relocDialog).findByText(/对象存储连接已变更/)).toBeInTheDocument();
    expect(within(relocDialog).getByRole("button", { name: "继续重试" })).toBeDisabled();
    expect(within(relocDialog).getByRole("button", { name: "取消收敛" })).toBeDisabled();
    expect(within(relocDialog).getByRole("button", { name: "旧桶不可达？强制断开" })).toBeDisabled();
  });

  it("计划读取失败后可重试，并在上传结束后刷新计划", async () => {
    const { plan, user, relocDialog } = await openInterruptedRelocation(1, true);
    expect(await within(relocDialog).findByText("读取计划失败")).toBeInTheDocument();
    const retryRun = within(relocDialog).getByRole("button", { name: "继续重试" });
    expect(retryRun).toBeDisabled();
    vi.mocked(API.getObjectStorageRelocationPlan).mockResolvedValueOnce({ ...plan, in_flight_uploads: 1 });
    await user.click(within(relocDialog).getByRole("button", { name: /^重试$/ }));
    expect(retryRun).toBeDisabled();
    await user.click(within(relocDialog).getByRole("button", { name: "刷新" }));
    expect(retryRun).toBeEnabled();
    expect(within(relocDialog).getByRole("checkbox")).toHaveAttribute("aria-disabled", "true");
  });

  it.each([false, true])("关闭中断弹窗后刷新连接并显示恢复横幅（进入强制断开 %s）", async (force) => {
    const { user, relocDialog } = await openInterruptedRelocation();
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
        ...LOCAL_ONLY, connection: { ...CONNECTION, relocating: true, relocate_mode: "delete" },
      });
    if (force) {
      await user.click(within(relocDialog).getByRole("button", { name: "旧桶不可达？强制断开" }));
      const dialog = await screen.findByRole("alertdialog", { name: "旧桶永久不可达，强制断开连接？" });
      await user.click(within(dialog).getByRole("button", { name: "取消" }));
    } else {
      await user.click(within(relocDialog).getByRole("button", { name: "关闭" }));
    }
    expect(await screen.findByText("旧桶图片收敛回本地尚未完成")).toBeInTheDocument();
  });

  it.each(["force", "relocate"])("完成 %s 后刷新失败只显示读取错误", async (mode) => {
    const { user, relocDialog } = await openInterruptedRelocation();
    vi.mocked(API.getObjectStorageConnection).mockRejectedValue(new Error("读取连接失败"));
    if (mode === "force") {
      await user.click(within(relocDialog).getByRole("button", { name: "旧桶不可达？强制断开" }));
      const force = await screen.findByRole("alertdialog", { name: "旧桶永久不可达，强制断开连接？" });
      await user.click(within(force).getByRole("button", { name: "确认强制断开" }));
    } else {
      const previous = await vi.mocked(API.runObjectStorageRelocation).mock.results[0].value;
      vi.mocked(API.runObjectStorageRelocation).mockResolvedValue({ ...previous, status: "completed" });
      await user.click(within(relocDialog).getByRole("button", { name: "继续重试" }));
    }
    expect(await screen.findByText("读取连接失败")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/已强制断开旧连接|旧桶图片已全部收敛为永久本地文件/)).not.toBeInTheDocument();
  });

  it("强制断开清单冲突后重新确认最新清单", async () => {
    const { user, relocDialog, missingItem, globalItem, plan, forceSpy } = await openInterruptedRelocation();
    await user.click(within(relocDialog).getByRole("button", { name: "旧桶不可达？强制断开" }));
    const forceDialog = await screen.findByRole("alertdialog", { name: "旧桶永久不可达，强制断开连接？" });
    expect(within(forceDialog).getByText("全局资产")).toBeInTheDocument();
    expect(within(forceDialog).getByText("已删除项目版本")).toBeInTheDocument();
    expect(within(forceDialog).getByText("demo / characters/Bob (v1)")).toBeInTheDocument();
    expect(within(forceDialog).getByText(/versions\/characters\/Bob_v1\.png/)).toBeInTheDocument();
    expect(within(forceDialog).getByRole("button", { name: "复制清单" })).toBeInTheDocument();

    forceSpy.mockRejectedValueOnce(new ApiRequestError("缺件清单已变化", undefined, 409));
    vi.mocked(API.getObjectStorageRelocationPlan).mockResolvedValue({ ...plan, unrecoverable_items: [globalItem] });
    await user.click(within(forceDialog).getByRole("button", { name: "确认强制断开" }));
    expect(await within(forceDialog).findByText("缺件清单已变化")).toBeInTheDocument();
    expect(within(forceDialog).queryByText(missingItem.holder_label)).not.toBeInTheDocument();
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue(LOCAL_ONLY);
    await user.click(within(forceDialog).getByRole("button", { name: "确认强制断开" }));
    expect(forceSpy).toHaveBeenLastCalledWith(1, [globalItem.confirmation_key]);
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
    expect(screen.getByRole("button", { name: "测试连接" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "继续收敛并删除" })).toBeInTheDocument();
    const targets = screen.getByRole("radiogroup", { name: "新增图片保存位置" });
    expect(within(targets).getByRole("radio", { name: /^对象存储/ })).toHaveAttribute("aria-disabled", "true");

    await user.click(screen.getByRole("button", { name: "取消收敛" }));
    expect(cancelSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText("旧桶图片收敛回本地尚未完成")).not.toBeInTheDocument());
  });

  it("无法读取影响清单时禁用强制断开确认", async () => {
    vi.mocked(API.getObjectStorageConnection).mockResolvedValue({
      connection: { ...CONNECTION, relocating: true },
      new_image_target: "local",
      pending_ingest_tasks: 0,
    });
    vi.spyOn(API, "getObjectStorageRelocationPlan").mockRejectedValue(new Error("offline"));
    const user = userEvent.setup();
    render(<MediaStorageSection />);
    await user.click(await screen.findByRole("button", { name: "旧桶不可达？强制断开" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(await within(dialog).findByRole("button", { name: "重试" })).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "确认强制断开" })).toBeDisabled();
  });
});
