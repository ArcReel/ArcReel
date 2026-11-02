import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API, ApiRequestError } from "@/api";
import type { AssetLibraryAccountList, ProviderAssetPage } from "@/types";

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

describe("ProviderAssetBrowserDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "listAssetLibraryAccounts").mockResolvedValue(ACCOUNTS);
  });

  it("按账号范围分页浏览素材的名称、类型与状态", async () => {
    vi.spyOn(API, "listProviderAssets").mockImplementation((_id, params) => Promise.resolve(page(params.page, 25)));
    const user = userEvent.setup();
    render(<ProviderAssetBrowserDialog open onOpenChange={() => {}} />);

    expect(await screen.findByText("主角正面 1")).toBeInTheDocument();
    expect(screen.getByText("可用")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("InputImageSensitiveContentDetected")).toBeInTheDocument();
    expect(screen.getByText("第 1/2 页，共 25 个")).toBeInTheDocument();
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
    render(<ProviderAssetBrowserDialog open onOpenChange={() => {}} />);

    expect(await screen.findByText("素材库拒绝了控制面凭证。")).toBeInTheDocument();
    expect(screen.queryByText("InvalidAccessKey: bad ak")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText("InvalidAccessKey: bad ak")).toBeInTheDocument();
  });

  it("没有素材库账号时引导到设置", async () => {
    vi.mocked(API.listAssetLibraryAccounts).mockResolvedValue({ ...ACCOUNTS, accounts: [] });
    const listAssets = vi.spyOn(API, "listProviderAssets");
    render(<ProviderAssetBrowserDialog open onOpenChange={() => {}} />);

    expect(await screen.findByRole("link", { name: "前往设置" })).toHaveAttribute(
      "href",
      expect.stringContaining("section=asset-library"),
    );
    expect(listAssets).not.toHaveBeenCalled();
  });
});
