import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import "@/i18n";
import { API } from "@/api";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { CustomProviderInfo, EndpointDescriptor } from "@/types";
import { ImageModelDualSelect } from "./ImageModelDualSelect";

const ENDPOINT_FIXTURE: EndpointDescriptor[] = [
  {
    key: "openai-images",
    media_type: "image",
    family: "openai",
    display_name_key: "endpoint_openai_images_display",
    request_method: "POST",
    request_path_template: "/v1/images/{generations,edits}",
    image_capabilities: ["text_to_image", "image_to_image"],
  },
  {
    key: "openai-images-generations",
    media_type: "image",
    family: "openai",
    display_name_key: "endpoint_openai_images_generations_display",
    request_method: "POST",
    request_path_template: "/v1/images/generations",
    image_capabilities: ["text_to_image"],
  },
  {
    key: "openai-images-edits",
    media_type: "image",
    family: "openai",
    display_name_key: "endpoint_openai_images_edits_display",
    request_method: "POST",
    request_path_template: "/v1/images/edits",
    image_capabilities: ["image_to_image"],
  },
];

// 一个含三类 endpoint 的自定义供应商：通配（双能力）/ 仅 T2I / 仅 I2I
const CUSTOM_PROVIDERS: CustomProviderInfo[] = [
  {
    id: 1,
    display_name: "Custom Open",
    discovery_format: "openai",
    base_url: "https://x.example.com",
    api_key_masked: "sk-***",
    created_at: "2026-01-01T00:00:00Z",
    models: [
      {
        id: 1,
        model_id: "wildcard-img",
        display_name: "Wildcard",
        endpoint: "openai-images",
        is_default: false,
        is_enabled: true,
        price_unit: null,
        price_input: null,
        price_output: null,
        currency: null,
        supported_durations: null,
        resolution: null,
      },
      {
        id: 2,
        model_id: "t2i-only",
        display_name: "T2I Only",
        endpoint: "openai-images-generations",
        is_default: false,
        is_enabled: true,
        price_unit: null,
        price_input: null,
        price_output: null,
        currency: null,
        supported_durations: null,
        resolution: null,
      },
      {
        id: 3,
        model_id: "i2i-only",
        display_name: "I2I Only",
        endpoint: "openai-images-edits",
        is_default: false,
        is_enabled: true,
        price_unit: null,
        price_input: null,
        price_output: null,
        currency: null,
        supported_durations: null,
        resolution: null,
      },
    ],
  },
];

// 与上面 customProviders 对齐的可选项字符串（system_config 路径下的 image_backends 列表形态）
const OPTIONS = [
  "gemini/imagen-4", // 内置：默认双能力
  "custom-1/wildcard-img", // 自定义双能力
  "custom-1/t2i-only", // 自定义仅 T2I
  "custom-1/i2i-only", // 自定义仅 I2I
];
const PROVIDER_NAMES = { gemini: "Gemini", "custom-1": "Custom Open" };

async function setupCatalog() {
  useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
  vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: ENDPOINT_FIXTURE });
  await useEndpointCatalogStore.getState().fetch();
}

describe("ImageModelDualSelect — 渐进式 UI", () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    await setupCatalog();
  });

  it("默认空值时只渲染 1 个下拉（单下拉模式）", () => {
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={() => {}}
      />,
    );
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("两槽同值（双能力模型）时仍只渲染 1 个下拉", () => {
    render(
      <ImageModelDualSelect
        valueT2I="custom-1/wildcard-img"
        valueI2I="custom-1/wildcard-img"
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={() => {}}
      />,
    );
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("从单下拉选双能力模型 → onChange 把 t2i 与 i2i 都置为同值", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /wildcard-img/ }));

    expect(onChange).toHaveBeenCalledWith({
      t2i: "custom-1/wildcard-img",
      i2i: "custom-1/wildcard-img",
    });
  });

  it("从单下拉选仅 T2I 模型 → onChange 仅置 t2i，i2i 留空", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /t2i-only/ }));

    expect(onChange).toHaveBeenCalledWith({
      t2i: "custom-1/t2i-only",
      i2i: "",
    });
  });

  it("从单下拉选仅 I2I 模型 → onChange 仅置 i2i，t2i 留空", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /i2i-only/ }));

    expect(onChange).toHaveBeenCalledWith({
      t2i: "",
      i2i: "custom-1/i2i-only",
    });
  });

  it("两槽值不一致时进入双下拉模式（2 个 combobox）", () => {
    render(
      <ImageModelDualSelect
        valueT2I="custom-1/t2i-only"
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={() => {}}
      />,
    );
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
  });

  it("双下拉 T2I 槽过滤掉仅 I2I 模型", async () => {
    const user = userEvent.setup();
    render(
      <ImageModelDualSelect
        valueT2I="custom-1/t2i-only"
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={() => {}}
      />,
    );

    const [t2iTrigger] = screen.getAllByRole("combobox");
    await user.click(t2iTrigger);

    expect(screen.queryByRole("option", { name: /i2i-only/ })).toBeNull();
    expect(screen.getByRole("option", { name: /t2i-only/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /wildcard-img/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /imagen-4/ })).toBeInTheDocument();
  });

  it("双下拉 I2I 槽过滤掉仅 T2I 模型", async () => {
    const user = userEvent.setup();
    render(
      <ImageModelDualSelect
        valueT2I="custom-1/t2i-only"
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={() => {}}
      />,
    );

    const [, i2iTrigger] = screen.getAllByRole("combobox");
    await user.click(i2iTrigger);

    expect(screen.queryByRole("option", { name: /t2i-only/ })).toBeNull();
    expect(screen.getByRole("option", { name: /i2i-only/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /wildcard-img/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /imagen-4/ })).toBeInTheDocument();
  });

  it("不传 customProviders 时所有选项按双能力处理（兼容路径）", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I=""
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        onChange={onChange}
      />,
    );

    // 即使是 t2i-only endpoint 的模型，因为没传 customProviders，也按双能力处理
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /t2i-only/ }));

    expect(onChange).toHaveBeenCalledWith({
      t2i: "custom-1/t2i-only",
      i2i: "custom-1/t2i-only",
    });
  });

  it("双下拉模式下 onChange 各自独立改两槽", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ImageModelDualSelect
        valueT2I="custom-1/t2i-only"
        valueI2I=""
        options={OPTIONS}
        providerNames={PROVIDER_NAMES}
        customProviders={CUSTOM_PROVIDERS}
        onChange={onChange}
      />,
    );

    const [, i2iTrigger] = screen.getAllByRole("combobox");
    await user.click(i2iTrigger);
    await user.click(screen.getByRole("option", { name: /i2i-only/ }));

    expect(onChange).toHaveBeenCalledWith({
      t2i: "custom-1/t2i-only",
      i2i: "custom-1/i2i-only",
    });
  });
});
