import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { EndpointDescriptor, EndpointRequest, ProviderModelDetail, ProviderModelItem } from "@/types";
import { ProviderModelSheet, type CustomProviderContext } from "./ProviderModelSheet";

const VIDEO_ENDPOINT: EndpointDescriptor = {
  key: "newapi-video",
  media_type: "video",
  family: "openai",
  kind: "declarative",
  source: "builtin",
  display_name_key: "endpoint_newapi_video",
  display_name: "视频生成",
  request_method: "POST",
  request_path_template: "/v1/video/generations",
  image_capabilities: null,
  end_image_capable: true,
  size_fixed: false,
  duration_fixed: false,
  duration_frame_rate_missing: false,
  duration_tier_empty: false,
  native_resolution: null,
  requests: [],
};

const SUBMIT: EndpointRequest = {
  name: "submit",
  display_name_key: "cp_request_submit",
  default_path: "/videos",
  placeholders: ["model"],
  base_url_rule: "as_is",
};

const CUSTOM: CustomProviderContext = { id: 3, baseUrl: "https://relay.example.invalid", protocol: "openai" };

function item(overrides: Partial<ProviderModelItem> = {}): ProviderModelItem {
  return {
    model_id: "relay-video",
    api_model_name: "relay-video",
    display_name: null,
    media_type: "video",
    enabled: true,
    is_default: false,
    official: false,
    flags: ["unpriced"],
    price: null,
    endpoint: "newapi-video",
    ...overrides,
  };
}

/** 自定义供应商下对应不上条目的视频模型：媒体类型来自调用端点，原值是运行时回落。 */
function unmatchedVideo(overrides: Partial<ProviderModelDetail> = {}): ProviderModelDetail {
  return {
    model: item(),
    multiplier: 1,
    provider_multiplier: 1,
    entry: null,
    capabilities: {
      media_type: "video",
      editable: ["durations", "duration_constraints", "resolutions", "first_frame", "last_frame", "audio_track"],
      base: {
        durations: [4, 8],
        duration_constraints: null,
        resolutions: null,
        first_frame: true,
        last_frame: true,
        audio_track: "controllable",
      },
      overrides: {},
      effective: {},
    },
    endpoint: "newapi-video",
    resolution: null,
    path_overrides: null,
    requests: [],
    ...overrides,
  };
}

function renderSheet(props: Partial<Parameters<typeof ProviderModelSheet>[0]> = {}) {
  const handlers = { onClose: vi.fn(), onSaved: vi.fn(), onRemoved: vi.fn() };
  render(
    <ProviderModelSheet
      providerId="custom-3"
      providerName="我的中转站"
      open={{ modelId: "relay-video", key: 1 }}
      custom={CUSTOM}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

function rowOf(label: string) {
  return screen.getByText(label, { selector: "span" }).closest("li")!;
}

describe("ProviderModelSheet", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [VIDEO_ENDPOINT] });
  });

  it("shows an unmatched video model by its endpoint's media type, with the runtime fallback as the original value, and saves a change", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue(unmatchedVideo());
    const update = vi.spyOn(API, "updateProviderModel").mockResolvedValue(
      unmatchedVideo({
        capabilities: { ...unmatchedVideo().capabilities, overrides: { durations: [5, 10] } },
      }),
    );
    const { onSaved } = renderSheet();

    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("这个供应商支持的参数");
    // 视频的维度，没有文本模型的维度
    expect(within(rowOf("可选时长")).getByText("4, 8 秒")).toBeInTheDocument();
    expect(within(rowOf("首帧")).getByText("支持")).toBeInTheDocument();
    expect(screen.queryByText("图片输入")).not.toBeInTheDocument();
    expect(screen.getByText("没有这个模型的能力和价格信息，调用费用会记为未定价。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "修改「可选时长」" }));
    fireEvent.change(screen.getByRole("textbox", { name: "「可选时长」的值" }), { target: { value: "5, 10" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("custom-3", "relay-video", { capability_overrides: { durations: [5, 10] } }),
    );
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ model_id: "relay-video" }), "relay-video");
  });

  it("clears the overrides when the last changed dimension is restored", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue(
      unmatchedVideo({ capabilities: { ...unmatchedVideo().capabilities, overrides: { durations: [5] } } }),
    );
    const update = vi.spyOn(API, "updateProviderModel").mockResolvedValue(unmatchedVideo());
    renderSheet();

    fireEvent.click(await screen.findByRole("button", { name: "恢复「可选时长」" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("custom-3", "relay-video", { capability_overrides: null }));
  });

  it("blocks the save while a parameter is invalid", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue(unmatchedVideo());
    const update = vi.spyOn(API, "updateProviderModel");
    renderSheet();

    fireEvent.click(await screen.findByRole("button", { name: "修改「可选时长」" }));
    fireEvent.change(screen.getByRole("textbox", { name: "「可选时长」的值" }), { target: { value: "8-4" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("「这个供应商支持的参数」里有填写不正确的值。");
    expect(update).not.toHaveBeenCalled();
  });

  it("hints the final multiplier when the provider multiplier is not 1", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue(unmatchedVideo({ provider_multiplier: 0.8 }));
    renderSheet();

    fireEvent.change(await screen.findByRole("textbox", { name: "倍率" }), { target: { value: "0.5" } });

    expect(screen.getByText("再乘供应商倍率 0.8，最终为 0.4。")).toBeInTheDocument();
  });

  it("only offers enabling on an official model", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue({
      ...unmatchedVideo(),
      model: item({ model_id: "veo-3", official: true, endpoint: null, flags: [] }),
      multiplier: null,
      capabilities: { media_type: "video", editable: [], base: {}, overrides: {}, effective: {} },
      endpoint: null,
    });
    renderSheet({ providerId: "gemini-aistudio", providerName: "Gemini", custom: undefined, open: { modelId: "veo-3", key: 1 } });

    expect(await screen.findByText("内置模型的其他设置由官方维护。")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "启用" })).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "设为默认" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "模型 ID" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "倍率" })).not.toBeInTheDocument();
    expect(screen.queryByText("这个供应商支持的参数")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /移除/ })).not.toBeInTheDocument();
  });

  it("warns that modified request URLs go back to default when the endpoint changes, and saves without them", async () => {
    const user = userEvent.setup();
    const requested = { ...VIDEO_ENDPOINT, requests: [SUBMIT] };
    vi.mocked(API.listEndpointCatalog).mockResolvedValue({
      endpoints: [requested, { ...requested, key: "xai-video", display_name: "xAI Video" }],
    });
    vi.spyOn(API, "getProviderModel").mockResolvedValue(
      unmatchedVideo({ path_overrides: { submit: "/api/videos" }, requests: [SUBMIT] }),
    );
    const update = vi.spyOn(API, "updateProviderModel").mockResolvedValue(unmatchedVideo());
    renderSheet();

    expect(await screen.findByRole("button", { name: "请求路径 /api/videos" })).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "调用端点" }));
    await user.click(await screen.findByRole("option", { name: /xAI Video/ }));

    expect(screen.getByText("更换调用端点后，修改过的请求地址会恢复默认。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "请求路径 /videos" })).toBeInTheDocument();
    // 参数随调用端点变化，换了端点先保存再改
    expect(screen.queryByRole("button", { name: "修改「可选时长」" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("custom-3", "relay-video", { endpoint: "xai-video", path_overrides: null }),
    );
  });

  it("opens the model's endpoint with a way back to this provider", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue(unmatchedVideo());
    renderSheet();

    expect(await screen.findByRole("link", { name: "在调用端点中打开" })).toHaveAttribute(
      "href",
      "/app/settings?section=endpoints&endpoint=newapi-video&from=3",
    );
  });

  it("confirms before removing, naming the settings and projects that use the model", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getProviderModel").mockResolvedValue(unmatchedVideo());
    vi.spyOn(API, "getProviderModelReferences").mockResolvedValue({
      settings: ["default_video_backend"],
      projects: [{ name: "demo", title: "演示" }],
    });
    const remove = vi.spyOn(API, "removeProviderModel").mockResolvedValue();
    const { onRemoved, onClose } = renderSheet();

    await user.click(await screen.findByRole("button", { name: "从「我的中转站」移除" }));
    const dialog = await screen.findByRole("alertdialog", { name: "从「我的中转站」移除「relay-video」？" });
    expect(remove).not.toHaveBeenCalled();
    expect(await within(dialog).findByText("全局设置：默认视频模型")).toBeInTheDocument();
    expect(within(dialog).getByText("项目：演示")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "移除" }));

    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith("relay-video"));
    expect(remove).toHaveBeenCalledWith("custom-3", "relay-video");
    expect(onClose).toHaveBeenCalled();
  });

  it("shows maker, official name and media type read-only and the facts as editable fields", async () => {
    vi.spyOn(API, "getProviderModel").mockResolvedValue(veoDetail());
    renderSheet();

    const info = await infoSection();
    expect(within(info).getByText("veo-3")).toBeInTheDocument();
    expect(within(info).getByText("信息由官方维护，随官方数据更新。")).toBeInTheDocument();
    expect(within(info).getByLabelText("显示名称")).toHaveValue("Veo 3");
    expect(within(info).getByRole("textbox", { name: "「可选时长」的值" })).toHaveValue("4, 6, 8");
    expect(within(info).queryByRole("button", { name: "恢复官方数据" })).not.toBeInTheDocument();
  });

  it("saves an edit of an official entry, then restores official data after confirming", async () => {
    const user = userEvent.setup();
    const shared = [{ provider_id: "gemini-vertex", display_name: "Vertex AI" }];
    const official = veoDetail({ shared_with: shared });
    const modified = veoDetail({
      display_name: "My Veo",
      source: "modified",
      official: { display_name: "Veo 3", facts: official.entry!.facts },
      shared_with: shared,
    });
    const get = vi.spyOn(API, "getProviderModel").mockResolvedValueOnce(official).mockResolvedValueOnce(official);
    const update = vi.spyOn(API, "updateProviderModel").mockResolvedValue(modified);
    const restore = vi.spyOn(API, "restoreCatalogEntry").mockResolvedValue();
    const { onSaved } = renderSheet();

    const info = await infoSection();
    expect(within(info).getByText("「Vertex AI」也使用这些信息，修改会一起生效。")).toBeInTheDocument();
    await user.clear(within(info).getByLabelText("显示名称"));
    await user.type(within(info).getByLabelText("显示名称"), "My Veo");
    // 保存前提示：停止跟随官方，共用的供应商一起改变
    expect(screen.getByText(/保存后，这个模型改用你修改的信息，不再随官方更新。/)).toHaveTextContent(
      "「Vertex AI」也会一起改变。",
    );

    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("custom-3", "relay-video", {
        entry: { maker: "google", name: "veo-3", media_type: "video", display_name: "My Veo" },
      }),
    );
    expect(await within(info).findByText("你修改过这些信息，官方更新不会覆盖。")).toBeInTheDocument();
    expect(screen.queryByText(/不再随官方更新。/)).not.toBeInTheDocument();
    expect(within(info).getByText("Veo 3", { selector: "span" })).toBeInTheDocument();

    await user.click(within(info).getByRole("button", { name: "恢复官方数据" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("你对「My Veo」所做的修改会被丢弃，无法撤销。「Vertex AI」也会一起恢复。");
    expect(restore).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "恢复官方数据" }));

    await waitFor(() =>
      expect(restore).toHaveBeenCalledWith({ maker: "google", name: "veo-3", media_type: "video" }),
    );
    await waitFor(() => expect(within(info).getByLabelText("显示名称")).toHaveValue("Veo 3"));
    expect(within(info).getByText("信息由官方维护，随官方数据更新。")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
    expect(onSaved).toHaveBeenLastCalledWith(official.model, "relay-video");
    // 恢复不经保存栏
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("shows the official value under a modified field and adopts it", async () => {
    const user = userEvent.setup();
    const base = veoDetail();
    vi.spyOn(API, "getProviderModel").mockResolvedValue(
      veoDetail({
        source: "modified",
        facts: { ...base.entry!.facts, durations: [5, 10] },
        official: { display_name: "Veo 3", facts: base.entry!.facts },
      }),
    );
    const update = vi.spyOn(API, "updateProviderModel").mockResolvedValue(base);
    renderSheet();

    const info = await infoSection();
    const durations = within(info).getByRole("textbox", { name: "「可选时长」的值" });
    expect(durations).toHaveValue("5, 10");
    expect(within(info).getByText("4, 6, 8 秒")).toBeInTheDocument();

    await user.click(within(info).getByRole("button", { name: "采用「可选时长」的官方值" }));

    expect(within(info).getByRole("textbox", { name: "「可选时长」的值" })).toHaveValue("4, 6, 8");
    expect(within(info).queryByText("4, 6, 8 秒")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("custom-3", "relay-video", {
        entry: { maker: "google", name: "veo-3", media_type: "video", facts: { durations: [4, 6, 8] } },
      }),
    );
  });
});

/** 自定义供应商下对应到 Veo 3 条目的视频模型。 */
function veoDetail(entry: Partial<NonNullable<ProviderModelDetail["entry"]>> = {}): ProviderModelDetail {
  return unmatchedVideo({
    model: item({ display_name: entry.display_name ?? "Veo 3", flags: [] }),
    entry: {
      maker: "google",
      name: "veo-3",
      display_name: "Veo 3",
      media_type: "video",
      aliases: [],
      discontinued: false,
      context_length: null,
      facts: {
        durations: [4, 6, 8],
        resolutions: ["720p", "1080p"],
        duration_constraints: { by_resolution: {}, with_reference_images: [] },
      },
      source: "official",
      official: null,
      shared_with: [],
      ...entry,
    },
  });
}

async function infoSection() {
  return (await screen.findByRole("heading", { name: "模型信息" })).closest("section")!;
}
