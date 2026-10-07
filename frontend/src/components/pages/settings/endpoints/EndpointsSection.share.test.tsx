import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { CustomEndpointInfo } from "@/types";
import {
  CATALOG,
  COMFYUI_MINE,
  MINE,
  chooseMenu,
  descriptor,
  renderSection,
  stubEndpointsSection,
} from "./endpoints-section-test-utils";

describe("EndpointsSection · share to official market", () => {
  const comfyui: CustomEndpointInfo = {
    ...COMFYUI_MINE,
    display_name: MINE.display_name,
    definition: { ...COMFYUI_MINE.definition, meta: MINE.definition.meta },
  };
  const OFFICIAL_ON = { available: true, enabled: true, notice_seen: true, instance_id: null };

  beforeEach(() => {
    stubEndpointsSection();
    // 目录里多一个 workflow 端点，两种端点的分享入口都要覆盖。
    useEndpointCatalogStore.setState({
      endpoints: [...CATALOG, descriptor({ key: "ce-8", kind: "comfyui" })],
      loading: false,
      initialized: true,
    });
    vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({ endpoints: [MINE, comfyui] });
    vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue({
      media_type: "video",
      savable: true,
      notes: [],
      import_shape: "comfyui_api_workflow",
      wrapped_definition: null,
      bindings: {
        prompt: {
          state: "auto_selected",
          notes: [],
          candidates: [{
            target: { node: "6", input: "text", class_type: "CLIPTextEncode" },
            score: 200,
            signals: [],
            selected: true,
            origin: "inferred",
            depth: null,
          }],
        },
      },
    });
  });

  it.each([MINE, comfyui])("shows local diagnostics first and updates the $kind status badge", async (endpoint) => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue(OFFICIAL_ON);
    vi.spyOn(API, "listMarketSubmissions").mockResolvedValue({ submissions: [] });
    const check = vi
      .spyOn(API, "checkMarketSubmission")
      .mockResolvedValueOnce({
        diagnostics: [{ file: "definition.json", path: "$", code: "val_ce_missing_field", message: "缺少字段 submit" }],
      })
      .mockResolvedValue({ diagnostics: [] });
    const create = vi.spyOn(API, "createMarketSubmission").mockResolvedValue({
      endpoint_id: endpoint.id,
      endpoint_key: endpoint.key,
      endpoint_display_name: "Example Video API",
      type: "endpoint",
      slug: "example-video",
      status: "open",
      pr_url: "https://github.com/ArcReel/arcreel-market/pull/101",
      stale: false,
    });
    renderSection(`section=endpoints&endpoint=${endpoint.key}`);

    await chooseMenu("分享到官方市场");
    const dialog = await screen.findByRole("dialog");
    // slug 默认由端点名称派生；诊断未清零前不可提交。
    expect(within(dialog).getByLabelText("slug 建议")).toHaveValue("example-video-api");
    expect(await within(dialog).findByText("缺少字段 submit")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "提交" })).toBeDisabled();

    await userEvent.clear(within(dialog).getByLabelText("slug 建议"));
    await userEvent.type(within(dialog).getByLabelText("slug 建议"), "example-video");
    expect(await within(dialog).findByText("本地校验通过，可以提交")).toBeInTheDocument();
    expect(check).toHaveBeenLastCalledWith(
      { endpoint_id: endpoint.id, slug: "example-video", icon: null }, expect.anything(),
    );
    await userEvent.type(within(dialog).getByLabelText("GitHub 用户名（可选）"), "octo-cat");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交" }));

    expect(create).toHaveBeenCalledWith({
      endpoint_id: endpoint.id, slug: "example-video", icon: null, github_username: "octo-cat",
    });
    expect(await screen.findByText("审核中")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看 PR" })).toHaveAttribute(
      "href",
      "https://github.com/ArcReel/arcreel-market/pull/101",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("submits only the latest icon choice and waits for its read to finish", async () => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue(OFFICIAL_ON);
    vi.spyOn(API, "listMarketSubmissions").mockResolvedValue({ submissions: [] });
    const check = vi.spyOn(API, "checkMarketSubmission").mockResolvedValue({ diagnostics: [] });
    const create = vi.spyOn(API, "createMarketSubmission").mockResolvedValue({
      endpoint_id: MINE.id,
      endpoint_key: MINE.key,
      endpoint_display_name: "Example Video API",
      type: "endpoint",
      slug: "example-video-api",
      status: "open",
      pr_url: "https://github.com/ArcReel/arcreel-market/pull/101",
      stale: false,
    });
    let finishStale!: (buffer: ArrayBuffer) => void;
    const stale = new File(["a"], "old.png", { type: "image/png" });
    stale.arrayBuffer = () => new Promise((resolve) => { finishStale = resolve; });
    let finishLatest!: (buffer: ArrayBuffer) => void;
    const latest = new File(["b"], "new.svg", { type: "image/svg+xml" });
    latest.arrayBuffer = () => new Promise((resolve) => { finishLatest = resolve; });
    renderSection(`section=endpoints&endpoint=${MINE.key}`);

    await chooseMenu("分享到官方市场");
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("本地校验通过，可以提交")).toBeInTheDocument();
    const input = within(dialog).getByTestId("market-share-icon-input");
    await userEvent.upload(input, stale);
    // 图标读取未完成时不可提交，否则会带着上一次的图标或不带图标出站。
    expect(within(dialog).getByRole("button", { name: "提交" })).toBeDisabled();
    await userEvent.upload(input, latest);
    finishLatest(new TextEncoder().encode("<svg/>").buffer);
    finishStale(new TextEncoder().encode("png").buffer);

    const latestIcon = { filename: "icon.svg", content: btoa("<svg/>") };
    await waitFor(() =>
      expect(check).toHaveBeenLastCalledWith(
        { endpoint_id: MINE.id, slug: "example-video-api", icon: latestIcon }, expect.anything(),
      ),
    );
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "提交" })).toBeEnabled());
    await userEvent.click(within(dialog).getByRole("button", { name: "提交" }));
    expect(create).toHaveBeenCalledWith({
      endpoint_id: MINE.id, slug: "example-video-api", icon: latestIcon, github_username: null,
    });
  });

  it.each([MINE, comfyui])("shows the refreshed $kind status on entering the page", async (endpoint) => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue(OFFICIAL_ON);
    vi.spyOn(API, "listMarketSubmissions").mockResolvedValue({
      submissions: [
        {
          endpoint_id: endpoint.id,
          endpoint_key: endpoint.key,
          endpoint_display_name: "Example Video API",
          type: "endpoint",
          slug: "example-video",
          status: "merged",
          pr_url: "https://github.com/ArcReel/arcreel-market/pull/101",
          stale: false,
        },
      ],
    });
    renderSection(`section=endpoints&endpoint=${endpoint.key}`);
    expect(await screen.findByText("已采纳")).toBeInTheDocument();
  });

  it.each([MINE, comfyui])("offers no $kind share action while the official service is off", async (endpoint) => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue({ ...OFFICIAL_ON, enabled: false });
    const list = vi.spyOn(API, "listMarketSubmissions");
    renderSection(`section=endpoints&endpoint=${endpoint.key}`);
    await userEvent.click(await screen.findByRole("button", { name: "更多操作" }));
    expect(await screen.findByRole("menuitem", { name: "删除端点" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "分享到官方市场" })).not.toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();
  });

  it("requires saving ComfyUI edits before sharing", async () => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue(OFFICIAL_ON);
    vi.spyOn(API, "listMarketSubmissions").mockResolvedValue({ submissions: [] });
    renderSection("section=endpoints&endpoint=ce-8");
    await screen.findByText("1 个节点");
    await userEvent.type(screen.getByLabelText("端点名称"), " changed");
    await userEvent.click(screen.getByRole("button", { name: "更多操作" }));
    expect(await screen.findByRole("menuitem", { name: "先保存修改，再分享" })).toHaveAttribute("aria-disabled", "true");
  });
});
