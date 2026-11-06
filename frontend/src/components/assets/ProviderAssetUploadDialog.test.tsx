import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { PROVIDER_ASSET_LOCAL_FAILURES } from "@/components/pages/settings/asset-library/asset-library-labels";
import enAssets from "@/i18n/en/assets";
import viAssets from "@/i18n/vi/assets";
import zhAssets from "@/i18n/zh/assets";
import localFailuresContract from "@/test/contracts/provider-asset-local-failures.json";
import type { ProviderAssetLink, ProviderAssetLinkPanel } from "@/types";

import { LINK_POLL_INTERVAL_MS, ProviderAssetUploadDialog } from "./ProviderAssetUploadDialog";

function link(overrides: Partial<ProviderAssetLink> = {}): ProviderAssetLink {
  return {
    id: 11,
    content_version_id: "cv-a",
    version: 2,
    account_id: 7,
    scope_key: "scope-7",
    site: "volcengine",
    region: "cn-beijing",
    provider_project_name: "film",
    state: "pending",
    selected: true,
    usable: false,
    provider_asset_id: null,
    error_code: null,
    error_message: null,
    check_error: null,
    polling: true,
    created_at: null,
    submitted_at: null,
    settled_at: null,
    ...overrides,
  };
}

function panel(links: ProviderAssetLink[], overrides: Partial<ProviderAssetLinkPanel> = {}): ProviderAssetLinkPanel {
  return {
    content_version: { id: "cv-a", version: 2, digest: "d" },
    object_storage_configured: true,
    accounts: [{ id: 7, scope_key: "scope-7", name: "主账号", site: "volcengine", region: "cn-beijing", project_name: "film" }],
    links,
    ...overrides,
  };
}

function renderDialog() {
  return render(
    <ProviderAssetUploadDialog open onOpenChange={() => {}} projectName="demo" resourceType="characters" resourceId="Alice" />,
  );
}

describe("ProviderAssetUploadDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("上传当前版本后轮询到可用状态，并显示素材 ID", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const getLinks = vi
      .spyOn(API, "getProviderAssetLinks")
      .mockResolvedValueOnce(panel([]))
      .mockResolvedValueOnce(panel([link({ state: "processing", provider_asset_id: "asset-1" })]))
      .mockResolvedValue(panel([link({ state: "active", usable: true, provider_asset_id: "asset-1", polling: false })]));
    const upload = vi.spyOn(API, "uploadToProviderAssetLibrary").mockResolvedValue(link());

    renderDialog();
    expect(await screen.findByText("当前版本：第 2 版")).toBeInTheDocument();
    expect(screen.getByText("未关联")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "上传到火山素材库" }));
    expect(upload).toHaveBeenCalledWith("demo", { resource_type: "characters", resource_id: "Alice", account_id: 7 });
    expect(await screen.findByText("供应商处理中")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LINK_POLL_INTERVAL_MS);
    });
    expect(await screen.findByText("可用")).toBeInTheDocument();
    expect(screen.getByText("素材 ID：asset-1")).toBeInTheDocument();
    const calls = getLinks.mock.calls.length;
    // 有定论后不再轮询
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LINK_POLL_INTERVAL_MS * 2);
    });
    expect(getLinks.mock.calls.length).toBe(calls);
  });

  it("解除关联先在行内确认，只解除关系", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getProviderAssetLinks")
      .mockResolvedValueOnce(panel([link({ state: "active", usable: true, provider_asset_id: "asset-1", polling: false })]))
      .mockResolvedValue(panel([]));
    const unlink = vi.spyOn(API, "unlinkProviderAsset").mockResolvedValue(undefined);

    renderDialog();
    await user.click(await screen.findByRole("button", { name: "解除关联" }));
    expect(screen.getByRole("group", { name: "只解除关系，不删除图片和供应商素材" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    expect(unlink).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "解除" }));
    expect(unlink).toHaveBeenCalledWith("demo", 11);
    expect(await screen.findByText("未关联")).toBeInTheDocument();
  });

  it("轮询一次失败后仍会继续获取最终状态", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(API, "getProviderAssetLinks")
      .mockResolvedValueOnce(panel([link({ state: "processing" })]))
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValue(panel([link({ state: "active", usable: true, provider_asset_id: "asset-1", polling: false })]));
    renderDialog();
    expect(await screen.findByText("供应商处理中")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(LINK_POLL_INTERVAL_MS));
    expect(screen.getByText("供应商处理中")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(LINK_POLL_INTERVAL_MS));
    expect(screen.getByText("可用")).toBeInTheDocument();
  });

  it("显示供应商的失败原因；没有对象存储时不能上传", async () => {
    vi.spyOn(API, "getProviderAssetLinks").mockResolvedValue(
      panel(
        [
          link({
            state: "failed",
            polling: false,
            error_code: "InputImageSensitiveContentDetected",
            error_message: "image rejected",
          }),
        ],
        {
          object_storage_configured: false,
          accounts: [
            { id: 7, scope_key: "scope-7", name: "主账号", site: "volcengine", region: "cn-beijing", project_name: "film" },
            { id: 8, scope_key: "scope-8", name: "海外账号", site: "byteplus", region: "ap-southeast-1", project_name: "default" },
          ],
        },
      ),
    );

    renderDialog();
    // 摘要只说原因与出路，供应商原文折叠在详情里
    expect(await screen.findByText("供应商未完成入库。解除关联后可以重新上传。")).toBeInTheDocument();
    expect(screen.queryByText(/InputImageSensitiveContentDetected/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText("InputImageSensitiveContentDetected: image rejected")).toBeInTheDocument();
    expect(screen.getByText(/上传需要经你的对象存储中转/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传到 BytePlus 素材库" })).toBeDisabled();
  });

  it("账号换成另一个云账号的 Access Key 后，旧关联单列为历史范围，账号行可以重新上传", async () => {
    vi.spyOn(API, "getProviderAssetLinks").mockResolvedValue(
      panel([link({ state: "active", usable: true, polling: false, provider_asset_id: "TEST-asset-a" })], {
        accounts: [{ id: 7, scope_key: "scope-7-new-key", name: "主账号", site: "volcengine", region: "cn-beijing", project_name: "film" }],
      }),
    );

    renderDialog();
    expect(await screen.findByText("账号范围已变更或账号已删除")).toBeInTheDocument();
    expect(screen.getByText("未关联")).toBeInTheDocument();
  });

  it("本地失败码表与后端一致，每个码在三种语言里都有原因文案", () => {
    expect([...PROVIDER_ASSET_LOCAL_FAILURES].sort()).toEqual(localFailuresContract);
    for (const messages of [zhAssets, enAssets, viAssets] as Record<string, string>[]) {
      for (const code of localFailuresContract) {
        expect(messages[`provider_asset_link_failure_${code}`]).toBeTruthy();
      }
    }
  });
});
