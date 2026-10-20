import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import userEvent from "@testing-library/user-event";
import type { FetchedProviderModel, ProviderModelDetail, ProviderModelItem } from "@/types";
import { ProviderModelList } from "./ProviderModelList";

function item(overrides: Partial<ProviderModelItem> & Pick<ProviderModelItem, "model_id">): ProviderModelItem {
  return {
    api_model_name: overrides.model_id,
    display_name: null,
    media_type: "text",
    enabled: true,
    is_default: false,
    official: true,
    flags: [],
    price: null,
    endpoint: null,
    ...overrides,
  };
}

function detailOf(model: ProviderModelItem): ProviderModelDetail {
  return {
    model,
    multiplier: 1,
    provider_multiplier: 1,
    entry: null,
    entry_binding: null,
    capabilities: { media_type: model.media_type, editable: [], base: {}, overrides: {}, effective: {} },
    endpoint: null,
    resolution: null,
    path_overrides: null,
    requests: [],
  };
}

function fetchedOf(modelId: string): FetchedProviderModel {
  return {
    model_id: modelId,
    display_name: modelId,
    media_type: "text",
    recognized: false,
    model_key: null,
    matched_entry: null,
    endpoint: null,
    endpoint_by_media_type: null,
    is_default: false,
  };
}

const MODELS: ProviderModelItem[] = [
  item({ model_id: "img-1", display_name: "Image One", media_type: "image", flags: ["modified"] }),
  item({
    model_id: "chat-1",
    display_name: "Chat One",
    is_default: true,
    price: {
      currency: "USD",
      items: [
        { meter: "uncached_input_tokens", min_price: 0.05, max_price: 0.05, per: 1_000_000 },
        { meter: "output_tokens", min_price: 0.15, max_price: 0.3, per: 1_000_000 },
      ],
    },
  }),
  item({ model_id: "chat-legacy", enabled: false, flags: ["discontinued", "unpriced"] }),
];

describe("ProviderModelList", () => {
  beforeEach(async () => {
    await act(async () => i18n.changeLanguage("zh"));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("groups models by media type with default badge, status flags and price summary", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS, discoverable: false });

    render(<ProviderModelList providerId="acme" providerName="Acme" />);

    const lists = await screen.findAllByRole("list");
    // 文本组在图片组之前
    expect(lists).toHaveLength(2);
    const [text, image] = lists;
    expect(within(text).getByText("Chat One")).toBeInTheDocument();
    expect(within(text).getByText("默认")).toBeInTheDocument();
    expect(within(text).getByText("$0.05/百万 token 输入，$0.15–0.3/百万 token 输出")).toBeInTheDocument();
    // 没有显示名时显示模型 ID
    expect(within(text).getAllByText("chat-legacy")).toHaveLength(2);
    expect(within(text).getByText("已下架")).toBeInTheDocument();
    expect(within(text).getByText("未定价")).toBeInTheDocument();
    expect(within(image).getByText("Image One")).toBeInTheDocument();
    // 改过模型信息的行带「已修改」
    expect(within(image).getByText("已修改")).toBeInTheDocument();
    // 没有模型列表接口的供应商没有「获取模型」
    expect(screen.queryByRole("button", { name: "获取模型" })).not.toBeInTheDocument();
  });

  it("filters to enabled models", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS, discoverable: false });

    render(<ProviderModelList providerId="acme" providerName="Acme" />);
    await screen.findByText("Chat One");
    fireEvent.click(screen.getByRole("button", { name: /已启用/ }));

    expect(screen.queryByText("chat-legacy")).not.toBeInTheDocument();
    expect(screen.getByText("Chat One")).toBeInTheDocument();
  });

  it("applies a toggle immediately and clears the default when disabling", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS, discoverable: false });
    const setEnabled = vi
      .spyOn(API, "setProviderModelEnabled")
      .mockResolvedValue({ ...MODELS[1], enabled: false, is_default: false });
    const onModelsChanged = vi.fn();

    render(<ProviderModelList providerId="acme" providerName="Acme" onModelsChanged={onModelsChanged} />);
    const toggle = await screen.findByRole("switch", { name: "启用「Chat One」" });
    fireEvent.click(toggle);

    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith("acme", "chat-1", false));
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(screen.queryByText("默认")).not.toBeInTheDocument();
    // 上层按已启用的模型判断（如自定义供应商不填密钥时的冲突提示），启停后要通知它
    expect(onModelsChanged).toHaveBeenCalledTimes(1);
  });

  it("refetches the rows when the saved provider multiplier changes", async () => {
    // 倍率为 0 时不标「未定价」：倍率跨过 0 后行上的标记要跟着变
    const list = vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS, discoverable: false });

    const { rerender } = render(<ProviderModelList providerId="acme" providerName="Acme" providerMultiplier={1} />);
    await screen.findByRole("switch", { name: "启用「Chat One」" });
    expect(list).toHaveBeenCalledTimes(1);

    rerender(<ProviderModelList providerId="acme" providerName="Acme" providerMultiplier={0} />);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("keeps the state and reports the reason when a toggle fails", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS, discoverable: false });
    vi.spyOn(API, "setProviderModelEnabled").mockRejectedValue(new Error("boom"));
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");

    render(<ProviderModelList providerId="acme" providerName="Acme" />);
    const toggle = await screen.findByRole("switch", { name: "启用「chat-legacy」" });
    fireEvent.click(toggle);

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith(expect.stringContaining("boom"), "error"));
    expect(toggle).not.toBeChecked();
  });

  it("adds a model by ID and opens the new model's Sheet", async () => {
    const user = userEvent.setup();
    const added = item({ model_id: "my-model", official: false });
    vi.spyOn(API, "listProviderModels")
      .mockResolvedValueOnce({ models: [], discoverable: false })
      .mockResolvedValue({ models: [added], discoverable: false });
    vi.spyOn(API, "searchCatalogEntries").mockResolvedValue({ entries: [] });
    const add = vi.spyOn(API, "addProviderModel").mockResolvedValue({ model: added, official: false });
    const getModel = vi.spyOn(API, "getProviderModel").mockResolvedValue(detailOf(added));
    const onModelsChanged = vi.fn();

    render(<ProviderModelList providerId="acme" providerName="Acme" onModelsChanged={onModelsChanged} />);
    await screen.findByText("这个供应商还没有模型。");
    await user.click(screen.getByRole("button", { name: "添加模型" }));
    await user.type(await screen.findByPlaceholderText("搜索模型名称，或输入模型 ID"), "my-model");
    await user.click(await screen.findByRole("option", { name: "添加「my-model」" }));

    await waitFor(() => expect(add).toHaveBeenCalledWith("acme", { model_id: "my-model" }));
    await waitFor(() => expect(getModel).toHaveBeenCalledWith("acme", "my-model", expect.anything()));
    expect(onModelsChanged).toHaveBeenCalled();
  });

  it("fills in a model the catalog does not have from the add dialog: writes the entry, then adds the model bound to it", async () => {
    const user = userEvent.setup();
    const added = item({ model_id: "relay-flux", official: false, media_type: "image" });
    vi.spyOn(API, "listProviderModels")
      .mockResolvedValueOnce({ models: [], discoverable: false })
      .mockResolvedValue({ models: [added], discoverable: false });
    vi.spyOn(API, "searchCatalogEntries").mockResolvedValue({ entries: [] });
    vi.spyOn(API, "getNewEntryHints").mockResolvedValue({ existing: null, durations: null });
    const create = vi
      .spyOn(API, "createCatalogEntry")
      .mockResolvedValue({ maker: "bfl", name: "flux-x", media_type: "image", display_name: "flux-x", discontinued: false });
    const add = vi.spyOn(API, "addProviderModel").mockResolvedValue({ model: added, official: false });
    vi.spyOn(API, "getProviderModel").mockResolvedValue(detailOf(added));

    render(<ProviderModelList providerId="acme" providerName="Acme" />);
    await screen.findByText("这个供应商还没有模型。");
    await user.click(screen.getByRole("button", { name: "添加模型" }));
    await user.type(await screen.findByPlaceholderText("搜索模型名称，或输入模型 ID"), "relay-flux");
    await user.click(await screen.findByRole("button", { name: "手动填写" }));

    const dialog = await screen.findByRole("dialog", { name: "手动填写模型信息" });
    // 模型名称取搜索词；添加模型时类型由用户选
    expect(within(dialog).getByLabelText("模型名称")).toHaveValue("relay-flux");
    await user.type(within(dialog).getByLabelText("模型厂商"), "bfl");
    await user.clear(within(dialog).getByLabelText("模型名称"));
    await user.type(within(dialog).getByLabelText("模型名称"), "flux-x");
    await user.click(within(dialog).getByRole("button", { name: "图片" }));
    await user.click(within(dialog).getByRole("button", { name: "确定" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        maker: "bfl",
        name: "flux-x",
        media_type: "image",
        facts: { text_to_image: true, image_to_image: false, resolutions: [] },
      }),
    );
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith("acme", {
        model_id: "relay-flux",
        media_type: "image",
        entry_binding: "bfl/flux-x",
      }),
    );
  });

  it("offers only the media types the provider supports when filling in a model by hand", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: [], discoverable: false });
    vi.spyOn(API, "searchCatalogEntries").mockResolvedValue({ entries: [] });
    vi.spyOn(API, "getNewEntryHints").mockResolvedValue({ existing: null, durations: null });

    render(<ProviderModelList providerId="kling" providerName="可灵" mediaTypes={["video", "image"]} />);
    await screen.findByText("这个供应商还没有模型。");
    await user.click(screen.getByRole("button", { name: "添加模型" }));
    await user.type(await screen.findByPlaceholderText("搜索模型名称，或输入模型 ID"), "kling-x");
    await user.click(await screen.findByRole("button", { name: "手动填写" }));

    const dialog = await screen.findByRole("dialog", { name: "手动填写模型信息" });
    expect(within(dialog).queryByRole("button", { name: "文本" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "音频" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "视频" })).toBeInTheDocument();
    // 默认选中第一个受支持的类型
    expect(within(dialog).getByRole("button", { name: "图片" })).toHaveAttribute("aria-pressed", "true");
  });

  it("leaves the output limit of a hand-filled text model unset unless the user gives one", async () => {
    // 预填一个常见值会作为模型事实写入，文本请求随之被压到这个上限；不填时按未登记处理
    const user = userEvent.setup();
    const added = item({ model_id: "relay-chat", official: false, media_type: "text" });
    vi.spyOn(API, "listProviderModels")
      .mockResolvedValueOnce({ models: [], discoverable: false })
      .mockResolvedValue({ models: [added], discoverable: false });
    vi.spyOn(API, "searchCatalogEntries").mockResolvedValue({ entries: [] });
    vi.spyOn(API, "getNewEntryHints").mockResolvedValue({ existing: null, durations: null });
    const create = vi
      .spyOn(API, "createCatalogEntry")
      .mockResolvedValue({ maker: "acme", name: "relay-chat", media_type: "text", display_name: "relay-chat", discontinued: false });
    vi.spyOn(API, "addProviderModel").mockResolvedValue({ model: added, official: false });
    vi.spyOn(API, "getProviderModel").mockResolvedValue(detailOf(added));

    render(<ProviderModelList providerId="acme" providerName="Acme" />);
    await screen.findByText("这个供应商还没有模型。");
    await user.click(screen.getByRole("button", { name: "添加模型" }));
    await user.type(await screen.findByPlaceholderText("搜索模型名称，或输入模型 ID"), "relay-chat");
    await user.click(await screen.findByRole("button", { name: "手动填写" }));

    const dialog = await screen.findByRole("dialog", { name: "手动填写模型信息" });
    await user.type(within(dialog).getByLabelText("模型厂商"), "acme");
    await user.click(within(dialog).getByRole("button", { name: "文本" }));
    await user.click(within(dialog).getByRole("button", { name: "确定" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        maker: "acme",
        name: "relay-chat",
        media_type: "text",
        facts: { image_input: false, structured_output: false },
      }),
    );
  });

  it("fetches models and adds a disabled official model by enabling it", async () => {
    const user = userEvent.setup();
    const disabled = item({ model_id: "chat-2", enabled: false });
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: [MODELS[1], disabled], discoverable: true });
    vi.spyOn(API, "discoverProviderModels").mockResolvedValue({
      not_applicable: false,
      reason: null,
      models: [
        { ...fetchedOf("chat-1"), recognized: true, model_key: "chat-1" },
        { ...fetchedOf("chat-2"), recognized: true, model_key: "chat-2" },
        fetchedOf("vendor-new"),
      ],
    });
    const add = vi
      .spyOn(API, "addProviderModel")
      .mockResolvedValue({ model: { ...disabled, enabled: true }, official: true });

    render(<ProviderModelList providerId="acme" providerName="Acme" />);
    await user.click(await screen.findByRole("button", { name: "获取模型" }));

    // 已启用的 chat-1 已添加；停用的官方收录模型列为已识别，添加即启用
    expect(await screen.findByRole("checkbox", { name: /chat-2/ })).toBeChecked();
    expect(screen.getByText("另有 1 个已添加的模型未列出。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "添加 1 个模型" }));

    await waitFor(() => expect(add).toHaveBeenCalledWith("acme", { model_id: "chat-2", media_type: "text" }));
  });

  it("opens a model's Sheet from its row and shows the renamed row after the Sheet saves", async () => {
    const own = item({ model_id: "my-chat", official: false });
    vi.spyOn(API, "listProviderModels")
      .mockResolvedValueOnce({ models: [own], discoverable: false })
      .mockResolvedValue({ models: [{ ...own, model_id: "my-chat-2", api_model_name: "my-chat-2" }], discoverable: false });
    const detail = {
      model: own,
      multiplier: 1,
      provider_multiplier: 1,
      entry: null,
      entry_binding: null,
      capabilities: { media_type: "text" as const, editable: [], base: {}, overrides: {}, effective: {} },
      endpoint: null,
      resolution: null,
      path_overrides: null,
      requests: [],
    };
    vi.spyOn(API, "getProviderModel").mockResolvedValue(detail);
    const update = vi
      .spyOn(API, "updateProviderModel")
      .mockResolvedValue({ ...detail, model: { ...own, model_id: "my-chat-2", api_model_name: "my-chat-2" } });
    const onModelsChanged = vi.fn();

    render(<ProviderModelList providerId="acme" providerName="Acme" onModelsChanged={onModelsChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: /my-chat/ }));
    const sheet = await screen.findByRole("dialog");
    fireEvent.change(await within(sheet).findByRole("textbox", { name: "模型 ID" }), { target: { value: "my-chat-2" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("acme", "my-chat", { model_id: "my-chat-2" }));
    expect(onModelsChanged).toHaveBeenCalled();
    expect(await screen.findAllByText("my-chat-2")).not.toHaveLength(0);
  });
});
