import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";
import type { AssetLibraryAccountList, ProviderAssetImportResult, ProviderAssetPage } from "@/types";

import { ProviderAssetBrowserDialog } from "./ProviderAssetBrowserDialog";

const ACCOUNTS: AssetLibraryAccountList = {
  sites: [{ id: "volcengine", regions: ["cn-beijing"] }],
  default_project_name: "default",
  accounts: [
    {
      id: 7,
      site: "volcengine",
      name: "主账号",
      region: "cn-beijing",
      project_name: "film",
      access_key_id_masked: "TEST…0001",
      secret_access_key_masked: "ctrl…0001",
      created_at: "2026-11-01T00:00:00Z",
    },
  ],
};

function page(number: number, total: number): ProviderAssetPage {
  return {
    items: [
      {
        id: `asset-${number}-a`,
        name: `主角正面 ${number}`,
        asset_type: "Image",
        status: "active",
        error_code: null,
        created_at: null,
      },
      {
        id: `asset-${number}-b`,
        name: "",
        asset_type: "Image",
        status: "failed",
        error_code: "InputImageSensitiveContentDetected",
        created_at: null,
      },
    ],
    total,
    page: number,
    page_size: 20,
  };
}

function renderDialog(props: Partial<React.ComponentProps<typeof ProviderAssetBrowserDialog>> = {}) {
  return render(
    <ProviderAssetBrowserDialog
      open
      onOpenChange={() => {}}
      projectName="demo"
      resourceType="characters"
      resourceId="林夕"
      onImported={async () => "success"}
      checkBusy={() => false}
      onImportingChange={() => {}}
      {...props}
    />,
  );
}

const IMPORTED: ProviderAssetImportResult = {
  content_version: { id: "cv-2", version: 2, digest: "d" },
  link: {
    id: 11,
    content_version_id: "cv-2",
    version: 2,
    account_id: 7,
    scope_key: "scope-7",
    site: "volcengine",
    region: "cn-beijing",
    provider_project_name: "film",
    state: "active",
    selected: true,
    usable: true,
    provider_asset_id: "asset-1-a",
    error_code: null,
    error_message: null,
    check_error: null,
    polling: false,
    created_at: null,
    submitted_at: null,
    settled_at: null,
  },
};

describe("ProviderAssetBrowserDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "listAssetLibraryAccounts").mockResolvedValue(ACCOUNTS);
  });

  it("按账号范围分页浏览素材的名称、类型与状态", async () => {
    vi.spyOn(API, "listProviderAssets").mockImplementation((_id, params) => Promise.resolve(page(params.page, 25)));
    const user = userEvent.setup();
    renderDialog();

    expect(await screen.findByText("主角正面 1")).toBeInTheDocument();
    expect(screen.getByText("可用")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("InputImageSensitiveContentDetected")).toBeInTheDocument();
    expect(screen.getByText("第 1/2 页，共 25 个")).toBeInTheDocument();
    // 只有可用的图片素材可以选择
    expect(screen.getByRole("button", { name: "选择 主角正面 1" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "选择 asset-1-b" })).not.toBeInTheDocument();
    expect(API.listProviderAssets).toHaveBeenLastCalledWith(7, { page: 1, pageSize: 20 }, expect.anything());

    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("主角正面 2")).toBeInTheDocument();
    expect(API.listProviderAssets).toHaveBeenLastCalledWith(7, { page: 2, pageSize: 20 }, expect.anything());
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it("供应商拒绝时先给出原因，原文折叠在详情里", async () => {
    vi.spyOn(API, "listProviderAssets").mockRejectedValue(
      new ApiRequestError("素材库拒绝了控制面凭证。", { code: "InvalidAccessKey", message: "bad ak" }, 502),
    );
    const user = userEvent.setup();
    renderDialog();

    expect(await screen.findByText("素材库拒绝了控制面凭证。")).toBeInTheDocument();
    expect(screen.queryByText("InvalidAccessKey: bad ak")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText("InvalidAccessKey: bad ak")).toBeInTheDocument();
  });

  it("没有素材库账号时引导到设置", async () => {
    vi.mocked(API.listAssetLibraryAccounts).mockResolvedValue({ ...ACCOUNTS, accounts: [] });
    const listAssets = vi.spyOn(API, "listProviderAssets");
    renderDialog();

    expect(await screen.findByRole("link", { name: "前往设置" })).toHaveAttribute(
      "href",
      expect.stringContaining("section=asset-library"),
    );
    expect(listAssets).not.toHaveBeenCalled();
  });

  it("选择可用图片后导入为新版本，刷新资产图并关闭", async () => {
    vi.spyOn(API, "listProviderAssets").mockResolvedValue(page(1, 2));
    const importAsset = vi.spyOn(API, "importProviderAsset").mockResolvedValue(IMPORTED);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onImported = vi.fn().mockResolvedValue("success");
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderDialog({ onImported, onOpenChange });

    await user.click(await screen.findByRole("button", { name: "选择 主角正面 1" }));

    expect(importAsset).toHaveBeenCalledWith("demo", {
      resource_type: "characters",
      resource_id: "林夕",
      account_id: 7,
      asset_id: "asset-1-a",
    });
    expect(onImported).toHaveBeenCalledWith(IMPORTED);
    expect(pushToast).toHaveBeenCalledWith("已导入为第 2 版", "success");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("取不回原图时提示原因，保持对话框打开，不刷新资产图", async () => {
    vi.spyOn(API, "listProviderAssets").mockResolvedValue(page(1, 2));
    vi.spyOn(API, "importProviderAsset").mockRejectedValue(
      new ApiRequestError("无法取回这个素材的图片内容，没有导入。", { reason: "http_status", status: 403 }, 502),
    );
    const onImported = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderDialog({ onImported, onOpenChange });

    await user.click(await screen.findByRole("button", { name: "选择 主角正面 1" }));

    expect(await screen.findByText("无法取回这个素材的图片内容，没有导入。")).toBeInTheDocument();
    expect(onImported).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "选择 主角正面 1" })).toBeEnabled();
  });

  it("导入在途通知卡片占用，刷新失败不同时提示成功", async () => {
    vi.spyOn(API, "listProviderAssets").mockResolvedValue(page(1, 2));
    const imported = createDeferred<ProviderAssetImportResult>();
    vi.spyOn(API, "importProviderAsset").mockReturnValue(imported.promise);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onImportingChange = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderDialog({ onImported: async () => "failed", onImportingChange, onOpenChange });
    await user.click(await screen.findByRole("button", { name: "选择 主角正面 1" }));
    expect(onImportingChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByText("关闭", { selector: "button" })).toBeDisabled();
    await act(async () => imported.resolve(IMPORTED));
    expect(onImportingChange).toHaveBeenLastCalledWith(false);
    expect(pushToast).not.toHaveBeenCalledWith(expect.anything(), "success");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
