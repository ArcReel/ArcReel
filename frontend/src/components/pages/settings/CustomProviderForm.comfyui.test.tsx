import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { EndpointDescriptor } from "@/types";
import { CustomProviderForm } from "./CustomProviderForm";

// ---------------------------------------------------------------------------
// CustomProviderForm behavior with discovery_format = comfyui.
// ---------------------------------------------------------------------------
// Five protocol differences: optional secrets, explanatory text replacing discovery, selectors listing only
// ComfyUI endpoints, no ending-frame overrides, video durations retained as overrides. Cover each plus
// controls proving other protocols unaffected.
// Also test wiring: endpoint-originated provider creation derives protocols from endpoints.

const CHAT_ENDPOINT: EndpointDescriptor = {
  key: "openai-chat",
  media_type: "text",
  family: "openai",
  kind: "python",
  source: "builtin",
  display_name_key: "endpoint_openai_chat_display",
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

const DECLARATIVE_VIDEO_ENDPOINT: EndpointDescriptor = {
  key: "ce-1",
  media_type: "video",
  family: "custom",
  kind: "declarative",
  source: "custom",
  display_name_key: "",
  display_name: "我的声明式端点",
  request_method: "POST",
  request_path_template: "/v1/video/create",
  image_capabilities: null,
  end_image_capable: true,
  size_fixed: false,
  duration_fixed: false,
  duration_frame_rate_missing: false,
  duration_tier_empty: false,
  native_resolution: null,
  requests: [],
};

const COMFYUI_VIDEO_ENDPOINT: EndpointDescriptor = {
  key: "ce-2",
  media_type: "video",
  family: "custom",
  kind: "comfyui",
  source: "custom",
  display_name_key: "",
  display_name: "我的 Wan workflow",
  request_method: "POST",
  request_path_template: "/prompt",
  image_capabilities: null,
  end_image_capable: false,
  size_fixed: false,
  duration_fixed: false,
  duration_frame_rate_missing: false,
  duration_tier_empty: false,
  native_resolution: null,
  requests: [],
};

const ALL_ENDPOINTS = [CHAT_ENDPOINT, DECLARATIVE_VIDEO_ENDPOINT, COMFYUI_VIDEO_ENDPOINT];

const CREATED = {
  id: 9,
  display_name: "我的 ComfyUI",
  discovery_format: "comfyui" as const,
  base_url: "http://comfy.invalid:8188",
  api_key_masked: "••••",
  models: [],
  created_at: "2026-01-01T00:00:00Z",
  image_max_workers: null,
  video_max_workers: null,
  audio_max_workers: null,
};

function renderForm(initialEndpoint?: string) {
  render(<CustomProviderForm onSaved={vi.fn()} initialEndpoint={initialEndpoint} />);
}

const PROTOCOL_LABELS: Record<string, string> = { openai: "OpenAI 兼容", google: "Google AI Studio", comfyui: "ComfyUI" };

function protocolPicker() {
  return screen.getByRole("combobox", { name: "模型发现协议" });
}

/** Choose model-discovery protocols in dropdowns. */
async function selectProtocol(format: string) {
  const user = userEvent.setup();
  await user.click(protocolPicker());
  await user.click(await screen.findByRole("option", { name: PROTOCOL_LABELS[format] }));
}

function endpointPicker() {
  return screen.getByRole("combobox", { name: "调用端点" });
}

/** Open endpoint selectors and return their listbox. */
async function openEndpointPicker() {
  await userEvent.setup().click(endpointPicker());
  return await screen.findByRole("listbox");
}

/** Failure reasons shown in save bars after saves. */
async function saveError() {
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  return await screen.findByRole("alert");
}

describe("CustomProviderForm（comfyui 协议）", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: ALL_ENDPOINTS });
    vi.spyOn(API, "createCustomProvider").mockRejectedValue(new Error("unexpected create"));
  });

  it("offers ComfyUI in the protocol dropdown", async () => {
    renderForm();

    await userEvent.setup().click(protocolPicker());

    expect(await screen.findByRole("option", { name: "ComfyUI" })).toBeInTheDocument();
  });

  it("switches to the ComfyUI protocol when wired from a ComfyUI endpoint", async () => {
    // Leaving protocols openai hides rows in selectors, while bidirectional pairing rejects saves.
    renderForm("ce-2");

    await waitFor(() => expect(protocolPicker()).toHaveTextContent("ComfyUI"));
  });

  it("leaves the protocol alone when wired from a declarative endpoint", async () => {
    renderForm("ce-1");
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));

    expect(protocolPicker()).toHaveTextContent("OpenAI 兼容");
  });

  it("does not undo a protocol the user picked after being wired in", async () => {
    renderForm("ce-2");
    await waitFor(() => expect(protocolPicker()).toHaveTextContent("ComfyUI"));

    await selectProtocol("openai");

    expect(protocolPicker()).toHaveTextContent("OpenAI 兼容");
  });

  it("re-points model rows that cannot hang on the new protocol", async () => {
    // Old values stay hidden by protocol filtering, discovered only through save-time 422s.
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    await selectProtocol("comfyui");

    expect(endpointPicker()).toHaveTextContent("我的 Wan workflow");
  });

  it("re-points them back when the protocol leaves ComfyUI", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    await selectProtocol("openai");

    expect(endpointPicker()).toHaveTextContent("OpenAI 文本");
  });

  it("parks rows with nowhere to hang and holds back the save", async () => {
    // No ComfyUI endpoints means nowhere to reassign. Old endpoints remain hidden in selectors,
    // yet saving submits and gets 422. Leave visibly unselected, blocking saves.
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, DECLARATIVE_VIDEO_ENDPOINT],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    await selectProtocol("comfyui");

    expect(endpointPicker()).toHaveTextContent("未选择端点");
    expect(screen.getByText(/有模型行还没选 ComfyUI 端点/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });
    expect(await saveError()).toHaveTextContent("有模型行还没选 ComfyUI 端点");
    expect(API.createCustomProvider).not.toHaveBeenCalled();
  });

  it("releases the save once the parked row gets an endpoint", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    await selectProtocol("comfyui");

    // Available compatible endpoints prevent unselected states and selection prompts.
    expect(endpointPicker()).toHaveTextContent("我的 Wan workflow");
    expect(screen.queryByText(/有模型行还没选 ComfyUI 端点/)).not.toBeInTheDocument();
  });

  it("writes a provider multiplier of 1 on create, since a ComfyUI provider defaults to 0", async () => {
    vi.mocked(API.createCustomProvider).mockResolvedValue(CREATED);
    vi.spyOn(API, "setProviderMultiplier").mockResolvedValue({ multiplier: 1, default: 0 });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });
    const multiplierInput = screen.getByRole("textbox", { name: "供应商倍率" });
    expect(multiplierInput).toHaveAttribute("placeholder", "默认 0");
    fireEvent.change(multiplierInput, { target: { value: "1" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(API.setProviderMultiplier).toHaveBeenCalledWith("custom-9", 1));
  });

  it("drops the capability override when the row is re-pointed onto ComfyUI", async () => {
    // ComfyUI capabilities derive from bindings; the protocol disables all overrides (server _check_protocol_constraints).
    // Hidden overrides leave old values invisible/uneditable, exposed only by save-time 422s.
    vi.mocked(API.createCustomProvider).mockResolvedValue(CREATED);
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    const listbox = await openEndpointPicker();
    await userEvent.setup().click(within(listbox).getByRole("option", { name: /我的声明式端点/ }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    // model_id/endpoint changes invalidate overrides, so set overrides afterward to retain them.
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });
    fireEvent.click(await screen.findByRole("radio", { name: "强制关" }));

    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(API.createCustomProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          models: [expect.objectContaining({ endpoint: "ce-2", capability_overrides: null })],
        }),
      ),
    );
  });

  it("lets an automatically re-pointed row be made the default", async () => {
    // Derive fallback replacements only while incompatible endpoints remain. User row edits must settle derived values, or subsequent
    // derivation clears defaults again as changed routes, making selections ineffective.
    vi.mocked(API.createCustomProvider).mockResolvedValue(CREATED);
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });

    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "将 wan-t2v 设为默认" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(API.createCustomProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          models: [expect.objectContaining({ endpoint: "ce-2", is_default: true })],
        }),
      ),
    );
  });

  it("re-points the rows once a late endpoint catalog arrives", async () => {
    // Catalog fetches are asynchronous and may be absent when protocols switch, preventing row classification. Switch-only
    // derivation leaves nobody to reevaluate after loading, retaining incompatible endpoints forever.
    let deliver: (value: { endpoints: EndpointDescriptor[] }) => void = () => undefined;
    vi.spyOn(API, "listEndpointCatalog").mockReturnValue(
      new Promise((resolve) => {
        deliver = resolve;
      }),
    );
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    await selectProtocol("comfyui");
    deliver({ endpoints: ALL_ENDPOINTS });

    expect(await screen.findByRole("combobox", { name: "调用端点" })).toHaveTextContent("我的 Wan workflow");
  });

  it("holds back the save while the wired endpoint is still unresolved", async () => {
    // Wired endpoints determine protocols; unresolved catalogs fall back openai, guaranteeing rejected submissions.
    // Fill secrets too: openai fallback requires them, and earlier validation otherwise prevents reaching this condition.
    vi.spyOn(API, "listEndpointCatalog").mockReturnValue(new Promise(() => undefined));
    renderForm("ce-2");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });

    expect(await saveError()).toHaveTextContent("端点目录还没加载完，这几行挂的端点能不能用还判不了。稍候再保存。");
    expect(API.createCustomProvider).not.toHaveBeenCalled();
  });

  it("replaces the discover button with an explanation", async () => {
    renderForm();
    expect(screen.getByRole("button", { name: "获取模型" })).toBeInTheDocument();

    await selectProtocol("comfyui");

    expect(screen.queryByRole("button", { name: "获取模型" })).not.toBeInTheDocument();
    expect(screen.getByText(/「获取模型」对 ComfyUI 不适用/)).toBeInTheDocument();
    await waitFor(() => expect(API.listEndpointCatalog).toHaveBeenCalled());
  });

  it("saves with an empty key", async () => {
    vi.mocked(API.createCustomProvider).mockResolvedValue({
      id: 9,
      display_name: "我的 ComfyUI",
      discovery_format: "comfyui",
      base_url: "http://comfy.invalid:8188",
      api_key_masked: "••••",
      models: [],
      created_at: "2026-01-01T00:00:00Z",
      image_max_workers: null,
      video_max_workers: null,
      audio_max_workers: null,
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));

    await selectProtocol("comfyui");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(API.createCustomProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          discovery_format: "comfyui",
          api_key: "",
          models: [expect.objectContaining({ model_id: "wan-t2v", endpoint: "ce-2" })],
        }),
      ),
    );
  });

  it("still requires a key on the other protocols", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));

    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
    expect(await saveError()).toHaveTextContent("请填写密钥");
  });

  it("lists only ComfyUI endpoints in the endpoint picker", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const listbox = await openEndpointPicker();

    expect(within(listbox).getByRole("option", { name: /我的 Wan workflow/ })).toBeInTheDocument();
    expect(within(listbox).queryByRole("option", { name: /我的声明式端点/ })).not.toBeInTheDocument();
    expect(within(listbox).queryByRole("option", { name: /Chat/i })).not.toBeInTheDocument();
  });

  it("keeps ComfyUI endpoints out of the picker on the other protocols", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const listbox = await openEndpointPicker();

    expect(within(listbox).getByRole("option", { name: /我的声明式端点/ })).toBeInTheDocument();
    expect(within(listbox).queryByRole("option", { name: /我的 Wan workflow/ })).not.toBeInTheDocument();
  });

  it("hides the capability override control on a ComfyUI model row", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    expect(screen.queryByRole("radiogroup", { name: "尾帧能力覆盖" })).not.toBeInTheDocument();
  });

  it("keeps the capability override control on a declarative video row", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const listbox = await openEndpointPicker();
    await userEvent.setup().click(within(listbox).getByRole("option", { name: /我的声明式端点/ }));

    expect(await screen.findByRole("radiogroup", { name: "尾帧能力覆盖" })).toBeInTheDocument();
  });

  it("shows the duration tier read-only when the workflow fixes its duration", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, { ...COMFYUI_VIDEO_ENDPOINT, duration_fixed: true, duration_tier_empty: true }],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const durations = screen.getByLabelText("支持秒数");
    expect(durations).toBeDisabled();
    expect(durations).toHaveAttribute("placeholder", "此 workflow 时长固定");
    // Disabled reasons require visible explanations, never title alone.
    expect(screen.getByText(/此 workflow 时长固定：帧数没有绑定到节点/)).toBeInTheDocument();
  });

  it("shows the duration tier read-only when the workflow has no frame rate to convert with", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      // Bound frames with unreadable frame-rate sources also yield empty tiers, but definitions are fixable, not inherently fixed-duration.
      endpoints: [
        CHAT_ENDPOINT,
        {
          ...COMFYUI_VIDEO_ENDPOINT,
          duration_fixed: false,
          duration_frame_rate_missing: true,
          duration_tier_empty: true,
        },
      ],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const durations = screen.getByLabelText("支持秒数");
    expect(durations).toBeDisabled();
    expect(durations).toHaveAttribute("placeholder", "此 workflow 未提供帧率，时长固定");
    expect(screen.getByText(/帧数已绑定，但这份定义里没有帧率来源/)).toBeInTheDocument();
    expect(screen.queryByText(/帧数没有绑定到节点/)).not.toBeInTheDocument();
  });

  it("shows the duration tier read-only when frames and frame rate convert to no whole second", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      // Bound frames/readable rates may yield no whole-second duration that writes back exactly, also empty tiers,
      // neither inherently fixed nor repairable through missing frame-rate bindings.
      endpoints: [
        CHAT_ENDPOINT,
        {
          ...COMFYUI_VIDEO_ENDPOINT,
          duration_fixed: false,
          duration_frame_rate_missing: false,
          duration_tier_empty: true,
        },
      ],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const durations = screen.getByLabelText("支持秒数");
    expect(durations).toBeDisabled();
    expect(durations).toHaveAttribute("placeholder", "此 workflow 换算不出整秒时长");
    expect(screen.getByText(/帧数已绑定、帧率也读得到，但换算不出一档/)).toBeInTheDocument();
    expect(screen.queryByText(/帧数没有绑定到节点/)).not.toBeInTheDocument();
    expect(screen.queryByText(/没有帧率来源/)).not.toBeInTheDocument();
  });

  it("keeps the duration tier editable when frames are bound", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    expect(screen.getByLabelText("支持秒数")).toBeEnabled();
  });

  it("writes the durations of a ComfyUI video row into the duration override", async () => {
    vi.mocked(API.createCustomProvider).mockResolvedValue(CREATED);
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });

    fireEvent.change(screen.getByLabelText("支持秒数"), { target: { value: "4, 8" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(API.createCustomProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          models: [expect.objectContaining({ endpoint: "ce-2", capability_overrides: { durations: [4, 8] } })],
        }),
      ),
    );
  });

  it("blocks the save on a malformed duration list", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });

    fireEvent.change(screen.getByLabelText("支持秒数"), { target: { value: "8-4" } });

    expect(await saveError()).toHaveTextContent("格式无效");
    expect(API.createCustomProvider).not.toHaveBeenCalled();
  });

  it("offers no duration input on a declarative video row", async () => {
    // Other endpoint durations come from entries; return legacy overrides unchanged, never edit here.
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const listbox = await openEndpointPicker();
    await userEvent.setup().click(within(listbox).getByRole("option", { name: /我的声明式端点/ }));

    expect(await screen.findByRole("radiogroup", { name: "尾帧能力覆盖" })).toBeInTheDocument();
    expect(screen.queryByLabelText("支持秒数")).not.toBeInTheDocument();
  });

  it("disables the resolution picker and names the native tier when the size is fixed", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [
        CHAT_ENDPOINT,
        { ...COMFYUI_VIDEO_ENDPOINT, size_fixed: true, native_resolution: "480p" },
      ],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const picker = screen.getByLabelText("分辨率");
    expect(picker).toBeDisabled();
    expect(picker).toHaveAttribute("placeholder", "workflow 原生（480p）");
    expect(screen.getByText(/此 workflow 尺寸固定：宽高没有绑定到节点/)).toBeInTheDocument();
  });

  it("says the workflow decides the size when there is no literal to name", async () => {
    // Unbound width/height disables selectors; generic Default (omit) misleadingly implies another selectable omission option.
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, { ...COMFYUI_VIDEO_ENDPOINT, size_fixed: true, native_resolution: null }],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const picker = screen.getByLabelText("分辨率");
    expect(picker).toBeDisabled();
    expect(picker).toHaveAttribute("placeholder", "尺寸由 workflow 决定");
  });

  it("keeps the resolution picker usable while still naming the native tier", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, { ...COMFYUI_VIDEO_ENDPOINT, native_resolution: "720p" }],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    await selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const picker = screen.getByLabelText("分辨率");
    expect(picker).toBeEnabled();
    expect(picker).toHaveAttribute("placeholder", "workflow 原生（720p）");
  });

  it("announces the per-protocol concurrency default of one", async () => {
    renderForm();
    expect(screen.getByLabelText("视频并发")).toHaveAttribute("placeholder", "默认");

    await selectProtocol("comfyui");

    expect(screen.getByLabelText("视频并发")).toHaveAttribute("placeholder", "默认 1");
    expect(screen.getByText(/该协议默认 1/)).toBeInTheDocument();
  });
});
