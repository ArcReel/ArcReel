import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { ProviderModelItem } from "@/types";
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

const MODELS: ProviderModelItem[] = [
  item({ model_id: "img-1", display_name: "Image One", media_type: "image" }),
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
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS });

    render(<ProviderModelList providerId="acme" />);

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
    expect(screen.getByRole("button", { name: /添加模型/ })).toBeDisabled();
  });

  it("filters to enabled models", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS });

    render(<ProviderModelList providerId="acme" />);
    await screen.findByText("Chat One");
    fireEvent.click(screen.getByRole("button", { name: /已启用/ }));

    expect(screen.queryByText("chat-legacy")).not.toBeInTheDocument();
    expect(screen.getByText("Chat One")).toBeInTheDocument();
  });

  it("applies a toggle immediately and clears the default when disabling", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS });
    const setEnabled = vi
      .spyOn(API, "setProviderModelEnabled")
      .mockResolvedValue({ ...MODELS[1], enabled: false, is_default: false });

    render(<ProviderModelList providerId="acme" />);
    const toggle = await screen.findByRole("switch", { name: "启用「Chat One」" });
    fireEvent.click(toggle);

    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith("acme", "chat-1", false));
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(screen.queryByText("默认")).not.toBeInTheDocument();
  });

  it("keeps the state and reports the reason when a toggle fails", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: MODELS });
    vi.spyOn(API, "setProviderModelEnabled").mockRejectedValue(new Error("boom"));
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");

    render(<ProviderModelList providerId="acme" />);
    const toggle = await screen.findByRole("switch", { name: "启用「chat-legacy」" });
    fireEvent.click(toggle);

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith(expect.stringContaining("boom"), "error"));
    expect(toggle).not.toBeChecked();
  });

  it("calls the add-model entry when provided", async () => {
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: [] });
    const onAddModel = vi.fn();

    render(<ProviderModelList providerId="acme" onAddModel={onAddModel} />);
    await screen.findByText("这个供应商还没有模型。");
    fireEvent.click(screen.getByRole("button", { name: /添加模型/ }));

    expect(onAddModel).toHaveBeenCalledTimes(1);
  });
});
