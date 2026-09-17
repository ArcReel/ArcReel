import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { EndpointDescriptor } from "@/types";
import { CustomProviderForm } from "./CustomProviderForm";

// ---------------------------------------------------------------------------
// CustomProviderForm —— discovery_format = comfyui 下的表单行为
// ---------------------------------------------------------------------------
// 该协议下表单有四处与别的协议不同：API Key 可留空、「发现模型」被说明取代、端点选择器只列
// ComfyUI 端点、模型行不给能力覆盖入口。四条各一例，并各配一条其他协议不受影响的对照。

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
};

const ALL_ENDPOINTS = [CHAT_ENDPOINT, DECLARATIVE_VIDEO_ENDPOINT, COMFYUI_VIDEO_ENDPOINT];

function renderForm() {
  render(<CustomProviderForm onSaved={vi.fn()} onCancel={vi.fn()} />);
}

/** 切到某个模型发现协议；协议下拉是原生 select，按 option 的 value 选。 */
function selectProtocol(format: string) {
  fireEvent.change(screen.getByLabelText("模型发现协议"), { target: { value: format } });
}

/** 打开端点选择器弹层并返回它的 listbox。 */
async function openEndpointPicker() {
  fireEvent.click(screen.getByRole("button", { name: "调用端点" }));
  return await screen.findByRole("listbox", { name: "调用端点" });
}

describe("CustomProviderForm（comfyui 协议）", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: ALL_ENDPOINTS });
    vi.spyOn(API, "createCustomProvider").mockRejectedValue(new Error("unexpected create"));
  });

  it("offers ComfyUI in the protocol dropdown", () => {
    renderForm();

    expect(within(screen.getByLabelText("模型发现协议")).getByRole("option", { name: "ComfyUI" })).toBeInTheDocument();
  });

  it("replaces the discover button with an explanation", async () => {
    renderForm();
    expect(screen.getByRole("button", { name: "获取模型列表" })).toBeInTheDocument();

    selectProtocol("comfyui");

    expect(screen.queryByRole("button", { name: "获取模型列表" })).not.toBeInTheDocument();
    expect(screen.getByText(/「发现模型」对 ComfyUI 不适用/)).toBeInTheDocument();
    await waitFor(() => expect(API.listEndpointCatalog).toHaveBeenCalled());
  });

  it("saves with an empty API key", async () => {
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

    selectProtocol("comfyui");
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "http://comfy.invalid:8188" } });
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

  it("still requires an API key on the other protocols", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));

    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的中转站" } });
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "https://api.example.invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: "请填写 API Key", tone: "error" }),
    );
  });

  it("lists only ComfyUI endpoints in the endpoint picker", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    selectProtocol("comfyui");
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
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    expect(screen.queryByRole("radiogroup", { name: "尾帧能力覆盖" })).not.toBeInTheDocument();
  });

  it("keeps the capability override control on a declarative video row", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const listbox = await openEndpointPicker();
    fireEvent.click(within(listbox).getByRole("option", { name: /我的声明式端点/ }));

    expect(await screen.findByRole("radiogroup", { name: "尾帧能力覆盖" })).toBeInTheDocument();
  });

  it("announces the per-protocol concurrency default of one", () => {
    renderForm();
    expect(screen.getByLabelText("视频并发")).toHaveAttribute("placeholder", "默认");

    selectProtocol("comfyui");

    expect(screen.getByLabelText("视频并发")).toHaveAttribute("placeholder", "默认 1");
    expect(screen.getByText(/该协议默认 1/)).toBeInTheDocument();
  });
});
