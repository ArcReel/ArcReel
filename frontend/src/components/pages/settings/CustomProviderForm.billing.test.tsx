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
// 模型不再自带价格：计价取对应条目的价目表，乘供应商倍率与模型倍率。表单只显示对应结果、
// 编辑两级倍率，并把条目绑定与能力覆盖原样回传（整表 PUT 省略即清空）。

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
    global_bucket_refs: [],
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
async function renderExisting(
  existing: CustomProviderInfo,
  providerMultiplier?: ProviderMultiplierInfo,
  focusModelId?: string,
) {
  render(
    <CustomProviderForm
      existing={existing}
      providerMultiplier={providerMultiplier}
      focusModelId={focusModelId}
      onSaved={vi.fn()}
    />,
  );
  await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
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
    vi.spyOn(API, "fullUpdateCustomProvider").mockImplementation((_, data) =>
      Promise.resolve(provider(data.models.map((m, i) => model(i + 1, m.model_id)))),
    );
    vi.spyOn(API, "getCustomProvider").mockRejectedValue(new Error("refetch not under test"));
    vi.spyOn(API, "setProviderMultiplier").mockImplementation((_, multiplier) =>
      Promise.resolve({ multiplier: multiplier ?? 1, default: 1 }),
    );
    vi.spyOn(API, "createCustomProvider").mockRejectedValue(new Error("unexpected create"));
  });

  describe("对应条目", () => {
    it("names the matched entry, and shows unpriced when nothing matches or the entry has no price", async () => {
      await renderExisting(
        provider([
          model(1, "gpt-4o", { matched_entry: GPT_4O }),
          model(2, "my-finetune", { matched_entry: null }),
          model(3, "gpt-4o-legacy", { matched_entry: { ...GPT_4O, name: "gpt-4o-legacy", priced: false } }),
        ]),
      );

      expect(within(rowOf("gpt-4o")).getByText("GPT-4o")).toBeInTheDocument();
      expect(within(rowOf("my-finetune")).getByText("未定价")).toBeInTheDocument();
      expect(within(rowOf("gpt-4o-legacy")).getByText("未定价")).toBeInTheDocument();
    });

    it("explains in the editor that calls to an unmatched model are recorded as unpriced", async () => {
      await renderExisting(provider([model(1, "my-finetune")]), undefined, "my-finetune");

      expect(screen.getByText("没有对应的模型条目，这个模型的调用会记为未定价。")).toBeInTheDocument();
    });

    it("shows the entry identity in the editor and warns when its price table is missing", async () => {
      await renderExisting(
        provider([model(1, "gpt-4o", { matched_entry: { ...GPT_4O, priced: false } })]),
        undefined,
        "gpt-4o",
      );

      expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();
      expect(screen.getByText(/对应的模型条目没有可用的价目表/)).toBeInTheDocument();
    });

    it("waits for the save before naming an entry for a manually added row", async () => {
      await renderExisting(provider([model(1, "gpt-4o", { matched_entry: GPT_4O })]));

      fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

      expect(within(rowOf("未填写模型 ID")).getByText("保存后显示")).toBeInTheDocument();
      expect(within(rowOf("未填写模型 ID")).queryByText("未定价")).not.toBeInTheDocument();
    });

    it("stops naming the old entry once the model ID is edited, and restores it when changed back", async () => {
      await renderExisting(provider([model(1, "gpt-4o", { matched_entry: GPT_4O })]), undefined, "gpt-4o");
      const modelId = screen.getByRole("textbox", { name: "模型 ID" });

      fireEvent.change(modelId, { target: { value: "gpt-4o-mini" } });
      expect(within(rowOf("gpt-4o-mini")).getByText("保存后显示")).toBeInTheDocument();

      fireEvent.change(modelId, { target: { value: "gpt-4o" } });
      expect(within(rowOf("gpt-4o")).getByText("GPT-4o")).toBeInTheDocument();
    });

    it("names the entry a discovered model maps to", async () => {
      vi.spyOn(API, "discoverModelsForProvider").mockResolvedValue({
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
      await renderExisting(provider([]));

      fireEvent.click(screen.getByRole("button", { name: "获取模型列表" }));

      expect(await within(await screen.findByRole("table")).findByText("GPT-4o")).toBeInTheDocument();
      expect(within(rowOf("relay-only")).getByText("未定价")).toBeInTheDocument();
    });
  });

  describe("模型倍率", () => {
    it("shows a non-default multiplier next to the entry", async () => {
      await renderExisting(provider([model(1, "gpt-4o", { matched_entry: GPT_4O, multiplier: 0.8 }), model(2, "gpt-4o-mini")]));

      expect(within(rowOf("gpt-4o")).getByText("×0.8")).toBeInTheDocument();
      expect(within(rowOf("gpt-4o-mini")).queryByText(/×/)).not.toBeInTheDocument();
    });

    it("round-trips the multiplier, entry binding and capability overrides it does not edit", async () => {
      // 整表 PUT：省略条目绑定或覆盖即清空。升级迁移会把旧的可选时长与最大输出长度写进覆盖，
      // 这些键在非 ComfyUI 行上已经没有输入框，保存时必须原封不动带回去。
      const overrides = { durations: [5, 10], max_output_tokens: 8192 };
      await renderExisting(
        provider([
          model(1, "gpt-4o", { multiplier: 0.8, entry_binding: "openai/gpt-4o", capability_overrides: overrides }),
          model(2, "relay-video", { endpoint: "newapi-video", capability_overrides: { durations: [5, 10] } }),
        ]),
      );
      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "改过的名字" } });

      save();

      await waitFor(() => expect(API.fullUpdateCustomProvider).toHaveBeenCalled());
      expect(vi.mocked(API.fullUpdateCustomProvider).mock.calls[0][1].models).toEqual([
        expect.objectContaining({
          model_id: "gpt-4o",
          multiplier: 0.8,
          entry_binding: "openai/gpt-4o",
          capability_overrides: overrides,
        }),
        expect.objectContaining({
          model_id: "relay-video",
          multiplier: 1,
          entry_binding: null,
          capability_overrides: { durations: [5, 10] },
        }),
      ]);
    });

    it("has no price, max output length or duration inputs on a non-ComfyUI row", async () => {
      await renderExisting(
        provider([model(1, "relay-video", { endpoint: "newapi-video", capability_overrides: { durations: [5, 10] } })]),
        undefined,
        "relay-video",
      );

      expect(screen.queryByLabelText("支持秒数")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("最大输出长度")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("输入价格")).not.toBeInTheDocument();
    });

    it("saves an edited multiplier as a number", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), undefined, "gpt-4o");
      const input = screen.getByRole("textbox", { name: "模型倍率" });
      expect(input).toHaveValue("1");

      fireEvent.change(input, { target: { value: "1.5" } });
      save();

      await waitFor(() =>
        expect(API.fullUpdateCustomProvider).toHaveBeenCalledWith(
          3,
          expect.objectContaining({ models: [expect.objectContaining({ multiplier: 1.5 })] }),
        ),
      );
    });

    it.each(["-1", "abc", "1e3", ""])("rejects %j as a multiplier and blocks the save", async (text) => {
      await renderExisting(provider([model(1, "gpt-4o")]), undefined, "gpt-4o");

      fireEvent.change(screen.getByRole("textbox", { name: "模型倍率" }), { target: { value: text } });

      expect(screen.getByRole("textbox", { name: "模型倍率" })).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByText("倍率须为不小于 0 的数。")).toBeInTheDocument();
      save();
      expect(await screen.findByRole("alert")).toHaveTextContent("模型倍率须为不小于 0 的数");
      expect(API.fullUpdateCustomProvider).not.toHaveBeenCalled();
    });

    it("accepts zero", async () => {
      await renderExisting(provider([model(1, "gpt-4o")]), undefined, "gpt-4o");

      fireEvent.change(screen.getByRole("textbox", { name: "模型倍率" }), { target: { value: "0" } });
      save();

      await waitFor(() =>
        expect(API.fullUpdateCustomProvider).toHaveBeenCalledWith(
          3,
          expect.objectContaining({ models: [expect.objectContaining({ multiplier: 0 })] }),
        ),
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

      expect(await screen.findByRole("alert")).toHaveTextContent("供应商与模型已保存，供应商倍率未保存：网关拒绝");
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
