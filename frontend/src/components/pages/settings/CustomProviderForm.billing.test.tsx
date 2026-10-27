import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CustomProviderInfo,
  CustomProviderModelInfo,
  EndpointDescriptor,
  MatchedCatalogEntry,
  ProviderMultiplierInfo,
} from "@/types";
import { CustomProviderForm } from "./CustomProviderForm";

// ---------------------------------------------------------------------------
// CustomProviderForm —— 计费相关：模型对应的条目、模型倍率与供应商倍率
// ---------------------------------------------------------------------------
// 模型不带价格：计价取对应条目的价目表，乘供应商倍率与模型倍率。新建表单的模型表显示对应结果、
// 编辑模型倍率；已保存的供应商只编辑供应商倍率，模型在模型列表与 Sheet 里逐个保存。

const CHAT_ENDPOINT: EndpointDescriptor = {
  key: "openai-chat",
  media_type: "text",
  family: "openai",
  kind: "python",
  source: "builtin",
  display_name_key: "endpoint_openai_chat",
  display_name: null,
  request_method: "POST",
  request_path_template: "/v1/chat/completions",
  image_capabilities: null,
  end_image_capable: false,
  size_fixed: false,
  duration_fixed: false,
  duration_frame_rate_missing: false,
  duration_tier_empty: false,
  native_resolution: null,
  requests: [],
};

const VIDEO_ENDPOINT: EndpointDescriptor = {
  ...CHAT_ENDPOINT,
  key: "newapi-video",
  media_type: "video",
  display_name: "视频生成",
  request_path_template: "/v1/video/generations",
  end_image_capable: true,
};

const GPT_4O: MatchedCatalogEntry = {
  maker: "openai",
  name: "gpt-4o",
  media_type: "text",
  display_name: "GPT-4o",
  priced: true,
};

function model(id: number, modelId: string, extra: Partial<CustomProviderModelInfo> = {}): CustomProviderModelInfo {
  return {
    id,
    model_id: modelId,
    display_name: modelId,
    endpoint: "openai-chat",
    is_default: false,
    is_enabled: true,
    resolution: null,
    multiplier: 1,
    entry_binding: null,
    matched_entry: null,
    capability_overrides: null,
    path_overrides: null,
    requests: [],
    system_capabilities: null,
    ...extra,
  };
}

function provider(models: CustomProviderModelInfo[]): CustomProviderInfo {
  return {
    id: 3,
    display_name: "我的中转站",
    discovery_format: "openai",
    base_url: "https://api.example.invalid",
    api_key_masked: "sk-***",
    models,
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
  };
}

/** 渲染已保存的供应商，并等端点目录就绪：目录未到时保存会被「端点目录还没加载完」拦下。 */
async function renderExisting(existing: CustomProviderInfo, providerMultiplier?: ProviderMultiplierInfo) {
  render(<CustomProviderForm existing={existing} providerMultiplier={providerMultiplier} onSaved={vi.fn()} />);
  await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
}

/** 新建表单：填好名称、接口地址与密钥。 */
function renderNew(onSaved = vi.fn()) {
  render(<CustomProviderForm onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
  fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
  fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
  return onSaved;
}

/** 新建表单里手动添加一行模型并填上模型 ID；新加的行默认展开。 */
function addModelRow(modelId: string) {
  fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
  fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: modelId } });
}

function rowOf(modelId: string) {
  return screen.getByRole("button", { name: `编辑 ${modelId}` }).closest("tr")!;
}

function save() {
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
}

describe("CustomProviderForm（计费）", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT, VIDEO_ENDPOINT] });
    vi.spyOn(API, "fullUpdateCustomProvider").mockResolvedValue(provider([]));
    vi.spyOn(API, "listProviderModels").mockResolvedValue({ models: [], discoverable: false });
    vi.spyOn(API, "getCustomProvider").mockRejectedValue(new Error("refetch not under test"));
    vi.spyOn(API, "setProviderMultiplier").mockImplementation((_, multiplier) =>
      Promise.resolve({ multiplier: multiplier ?? 1, default: 1 }),
    );
    vi.spyOn(API, "createCustomProvider").mockRejectedValue(new Error("unexpected create"));
  });

  describe("对应条目", () => {
    it("waits for the save before naming an entry for a manually added row", async () => {
      renderNew();

      fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

      expect(within(rowOf("未填写模型 ID")).getByText("保存后显示")).toBeInTheDocument();
      expect(within(rowOf("未填写模型 ID")).queryByText("未定价")).not.toBeInTheDocument();
    });

    it("names the entry a discovered model maps to, and shows unpriced when nothing matches", async () => {
      vi.spyOn(API, "discoverModels").mockResolvedValue({
        not_applicable: false,
        reason: null,
        models: [
          {
            model_id: "gpt-4o",
            display_name: "gpt-4o",
            endpoint: "openai-chat",
            is_default: false,
            is_enabled: true,
            matched_entry: GPT_4O,
          },
          {
            model_id: "relay-only",
            display_name: "relay-only",
            endpoint: "openai-chat",
            is_default: false,
            is_enabled: true,
            matched_entry: null,
          },
        ],
      });
      renderNew();

      fireEvent.click(screen.getByRole("button", { name: "获取模型" }));
      // 对应不上条目的模型默认不勾选
      fireEvent.click(await screen.findByRole("checkbox", { name: /relay-only/ }));
      fireEvent.click(screen.getByRole("button", { name: "添加 2 个模型" }));

      expect(await within(await screen.findByRole("table")).findByText("GPT-4o")).toBeInTheDocument();
      expect(within(rowOf("relay-only")).getByText("未定价")).toBeInTheDocument();
    });
  });

  describe("模型倍率", () => {
    it("saves an edited multiplier of a new model as a number", async () => {
      vi.mocked(API.createCustomProvider).mockResolvedValue({ ...provider([]), id: 12 });
      const onSaved = renderNew();
      addModelRow("gpt-4o");
      const input = screen.getByRole("textbox", { name: "模型倍率" });
      expect(input).toHaveValue("1");

      fireEvent.change(input, { target: { value: "1.5" } });
      save();

      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      expect(API.createCustomProvider).toHaveBeenCalledWith(
        expect.objectContaining({ models: [expect.objectContaining({ model_id: "gpt-4o", multiplier: 1.5 })] }),
      );
    });

    it.each(["-1", "abc", "1e3", ""])("rejects %j as a multiplier and blocks the save", async (text) => {
      renderNew();
      addModelRow("gpt-4o");

      fireEvent.change(screen.getByRole("textbox", { name: "模型倍率" }), { target: { value: text } });

      expect(screen.getByRole("textbox", { name: "模型倍率" })).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByText("倍率须为不小于 0 的数。")).toBeInTheDocument();
      save();
      expect(await screen.findByRole("alert")).toHaveTextContent("模型倍率须为不小于 0 的数");
      expect(API.createCustomProvider).not.toHaveBeenCalled();
    });

    it("accepts zero", async () => {
      vi.mocked(API.createCustomProvider).mockResolvedValue({ ...provider([]), id: 12 });
      const onSaved = renderNew();
      addModelRow("gpt-4o");

      fireEvent.change(screen.getByRole("textbox", { name: "模型倍率" }), { target: { value: "0" } });
      save();

      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      expect(API.createCustomProvider).toHaveBeenCalledWith(
        expect.objectContaining({ models: [expect.objectContaining({ multiplier: 0 })] }),
      );
    });
  });

  describe("供应商倍率", () => {
    function providerMultiplierInput() {
      return screen.getByRole("textbox", { name: "供应商倍率" });
    }

    it("shows a customised value and names the default in the placeholder", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 0.9, default: 1 });

      expect(providerMultiplierInput()).toHaveValue("0.9");
      expect(providerMultiplierInput()).toHaveAttribute("placeholder", "默认 1");
    });

    it("leaves the input empty while the provider follows the default", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 1, default: 1 });

      expect(providerMultiplierInput()).toHaveValue("");
    });

    it("writes an edited value through the provider multiplier endpoint", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 0.9, default: 1 });

      fireEvent.change(providerMultiplierInput(), { target: { value: "0.7" } });
      save();

      await waitFor(() => expect(API.setProviderMultiplier).toHaveBeenCalledWith("custom-3", 0.7));
      expect(API.fullUpdateCustomProvider).toHaveBeenCalled();
      await waitFor(() => expect(screen.getByRole("button", { name: "保存" })).toBeDisabled());
    });

    it("resets to the default when the input is cleared", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 0.9, default: 1 });

      fireEvent.change(providerMultiplierInput(), { target: { value: "" } });
      save();

      await waitFor(() => expect(API.setProviderMultiplier).toHaveBeenCalledWith("custom-3", null));
    });

    it("leaves the provider multiplier alone when only other fields changed", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 0.9, default: 1 });

      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "改过的名字" } });
      save();

      await waitFor(() => expect(API.fullUpdateCustomProvider).toHaveBeenCalled());
      expect(API.setProviderMultiplier).not.toHaveBeenCalled();
    });

    it("blocks the save on an invalid value", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 1, default: 1 });

      fireEvent.change(providerMultiplierInput(), { target: { value: "-0.5" } });
      save();

      expect(await screen.findByRole("alert")).toHaveTextContent("供应商倍率须为不小于 0 的数。");
      expect(API.fullUpdateCustomProvider).not.toHaveBeenCalled();
      expect(API.setProviderMultiplier).not.toHaveBeenCalled();
    });

    it("reports a partial save when the multiplier fails after the provider was saved", async () => {
      vi.mocked(API.setProviderMultiplier).mockRejectedValue(new Error("网关拒绝"));
      await renderExisting(provider([model(1, "gpt-4o")]), { multiplier: 1, default: 1 });

      fireEvent.change(providerMultiplierInput(), { target: { value: "0.5" } });
      save();

      expect(await screen.findByRole("alert")).toHaveTextContent("供应商已保存，供应商倍率未保存：网关拒绝");
      // 未保存的只剩倍率：输入保留，保存按钮仍可用
      expect(providerMultiplierInput()).toHaveValue("0.5");
      expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
    });

    it("applies the multiplier to a newly created provider once it has an id", async () => {
      const onSaved = vi.fn();
      vi.mocked(API.createCustomProvider).mockResolvedValue({ ...provider([]), id: 12 });
      render(<CustomProviderForm onSaved={onSaved} />);
      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
      fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
      fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
      fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
      fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
      fireEvent.change(providerMultiplierInput(), { target: { value: "0.5" } });

      save();

      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      expect(API.setProviderMultiplier).toHaveBeenCalledWith("custom-12", 0.5);
      expect(vi.mocked(API.createCustomProvider).mock.invocationCallOrder[0]).toBeLessThan(
        vi.mocked(API.setProviderMultiplier).mock.invocationCallOrder[0],
      );
    });

    it("still hands over a created provider when its multiplier fails to save", async () => {
      const onSaved = vi.fn();
      vi.mocked(API.createCustomProvider).mockResolvedValue({ ...provider([]), id: 12 });
      vi.mocked(API.setProviderMultiplier).mockRejectedValue(new Error("网关拒绝"));
      render(<CustomProviderForm onSaved={onSaved} />);
      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
      fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
      fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
      fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
      fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
      fireEvent.change(providerMultiplierInput(), { target: { value: "0.5" } });

      save();

      // 供应商已经建好：照常交给宿主切过去，倍率失败另行提示，不能让用户再点一次保存建出第二个
      await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 12 })));
      expect(useAppStore.getState().toast?.text).toBe(
        "供应商已创建，供应商倍率未保存：网关拒绝。请在供应商详情里重新填写。",
      );
    });

    it("does not call the multiplier endpoint for a new provider left on the default", async () => {
      const onSaved = vi.fn();
      vi.mocked(API.createCustomProvider).mockResolvedValue({ ...provider([]), id: 12 });
      render(<CustomProviderForm onSaved={onSaved} />);
      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
      fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
      fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
      fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
      fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });

      save();

      await waitFor(() => expect(onSaved).toHaveBeenCalled());
      expect(API.setProviderMultiplier).not.toHaveBeenCalled();
    });
  });
});
