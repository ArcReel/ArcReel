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
// 另收一例接线：从某个端点跳过来新建供应商时，协议由那个端点定。

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
  duration_tier_empty: false,
  native_resolution: null,
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
  duration_tier_empty: false,
  native_resolution: null,
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
  duration_tier_empty: false,
  native_resolution: null,
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
  render(<CustomProviderForm onSaved={vi.fn()} onCancel={vi.fn()} initialEndpoint={initialEndpoint} />);
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

  it("switches to the ComfyUI protocol when wired from a ComfyUI endpoint", async () => {
    // 协议留在 openai 上时那一行在端点选择器里是隐着的，保存又会被双向配对拒掉。
    renderForm("ce-2");

    await waitFor(() => expect(screen.getByLabelText("模型发现协议")).toHaveValue("comfyui"));
  });

  it("leaves the protocol alone when wired from a declarative endpoint", async () => {
    renderForm("ce-1");
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));

    expect(screen.getByLabelText("模型发现协议")).toHaveValue("openai");
  });

  it("does not undo a protocol the user picked after being wired in", async () => {
    renderForm("ce-2");
    await waitFor(() => expect(screen.getByLabelText("模型发现协议")).toHaveValue("comfyui"));

    selectProtocol("openai");

    expect(screen.getByLabelText("模型发现协议")).toHaveValue("openai");
  });

  it("re-points model rows that cannot hang on the new protocol", async () => {
    // 留着旧值，那一行在端点选择器里是隐着的（选择器按协议过滤），保存时才吃 422。
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    selectProtocol("comfyui");

    expect(screen.getByRole("button", { name: "调用端点" })).toHaveTextContent("我的 Wan workflow");
  });

  it("re-points them back when the protocol leaves ComfyUI", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    selectProtocol("openai");

    expect(screen.getByRole("button", { name: "调用端点" })).toHaveTextContent("OpenAI 文本");
  });

  it("parks rows with nowhere to hang and holds back the save", async () => {
    // 一个 ComfyUI 端点都还没有：没有可改挂的去处，留着旧端点那一行在选择器里是隐着的，
    // 保存照样提交、照样吃 422。停在未选择上，用户看得见，保存也走不了。
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, DECLARATIVE_VIDEO_ENDPOINT],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    selectProtocol("comfyui");

    expect(screen.getByRole("button", { name: "调用端点" })).toHaveTextContent("未选择端点");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByText(/有模型行还没选 ComfyUI 端点/)).toBeInTheDocument();
  });

  it("releases the save once the parked row gets an endpoint", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    selectProtocol("comfyui");

    // 有 ComfyUI 端点可挂时根本不会停在未选择上，保存照常可用。
    expect(screen.getByRole("button", { name: "调用端点" })).toHaveTextContent("我的 Wan workflow");
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
  });

  it("drops the capability override when the row is re-pointed onto ComfyUI", async () => {
    // ComfyUI 端点的能力由节点绑定推导，该协议整个关闭覆盖（服务端 _check_protocol_constraints）。
    // 覆盖控件在这个协议下是藏起来的，留着旧值用户既看不见也改不掉，保存时才吃 422。
    vi.mocked(API.createCustomProvider).mockResolvedValue(CREATED);
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    const listbox = await openEndpointPicker();
    fireEvent.click(within(listbox).getByRole("option", { name: /我的声明式端点/ }));
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "http://comfy.invalid:8188" } });
    // model_id 与 endpoint 一改，覆盖本就随之作废；覆盖要设在它们之后才留得住。
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });
    fireEvent.click(await screen.findByRole("radio", { name: "强制关" }));

    selectProtocol("comfyui");
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
    // 派生只在行还挂着挂不住的端点时兜底改写；用户一动这一行，派生结果就该坐实，否则默认标记
    // 会被下一次派生按「换了一路即作废」再清一遍，点了等于没点。
    vi.mocked(API.createCustomProvider).mockResolvedValue(CREATED);
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });

    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "默认" }));
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
    // 端点目录是异步取的：切协议那一刻它可能还没回来，那一刻一行都判不了。判定若只在切换时算一次，
    // 目录到齐后没人再算，那一行就一直挂着挂不住的端点。
    let deliver: (value: { endpoints: EndpointDescriptor[] }) => void = () => undefined;
    vi.spyOn(API, "listEndpointCatalog").mockReturnValue(
      new Promise((resolve) => {
        deliver = resolve;
      }),
    );
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    selectProtocol("comfyui");
    deliver({ endpoints: ALL_ENDPOINTS });

    expect(await screen.findByRole("button", { name: "调用端点" })).toHaveTextContent("我的 Wan workflow");
  });

  it("holds back the save while the wired endpoint is still unresolved", async () => {
    // 接线过来的端点定协议，它还没在目录里解析出来时协议回退成 openai：照这份提交必被拒。
    // 密钥也一并填上——协议回退成 openai 时它是必填的，那条校验排在前面，不填就走不到这一步。
    vi.spyOn(API, "listEndpointCatalog").mockReturnValue(new Promise(() => undefined));
    renderForm("ce-2");
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的 ComfyUI" } });
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "http://comfy.invalid:8188" } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: "sk-live" } });
    fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "wan-t2v" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "端点目录还没加载完，这几行挂的端点能不能用还判不了。稍候再保存。",
        tone: "error",
      }),
    );
    expect(API.createCustomProvider).not.toHaveBeenCalled();
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

  it("shows the duration tier read-only when the workflow fixes its duration", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, { ...COMFYUI_VIDEO_ENDPOINT, duration_fixed: true, duration_tier_empty: true }],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const durations = screen.getByLabelText("支持秒数");
    expect(durations).toBeDisabled();
    expect(durations).toHaveAttribute("placeholder", "此 workflow 时长固定");
    // 禁用原因要有一行可见说明，不能只靠 title。
    expect(screen.getByText(/此 workflow 时长固定：帧数没有绑定到节点/)).toBeInTheDocument();
  });

  it("shows the duration tier read-only when the workflow has no frame rate to convert with", async () => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      // frames 绑了、读不到帧率来源：档位同样是空集，但这是一份可修的定义，不能说成「时长天生固定」。
      endpoints: [CHAT_ENDPOINT, { ...COMFYUI_VIDEO_ENDPOINT, duration_fixed: false, duration_tier_empty: true }],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const durations = screen.getByLabelText("支持秒数");
    expect(durations).toBeDisabled();
    expect(durations).toHaveAttribute("placeholder", "此 workflow 未提供帧率，时长固定");
    expect(screen.getByText(/帧数已绑定，但这份定义里没有帧率来源/)).toBeInTheDocument();
    expect(screen.queryByText(/帧数没有绑定到节点/)).not.toBeInTheDocument();
  });

  it("keeps the duration tier editable when frames are bound", async () => {
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    expect(screen.getByLabelText("支持秒数")).toBeEnabled();
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
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const picker = screen.getByLabelText("分辨率");
    expect(picker).toBeDisabled();
    expect(picker).toHaveAttribute("placeholder", "workflow 原生（480p）");
    expect(screen.getByText(/此 workflow 尺寸固定：宽高没有绑定到节点/)).toBeInTheDocument();
  });

  it("says the workflow decides the size when there is no literal to name", async () => {
    // 宽高两侧都没绑定：选择器已禁用，通用的「默认（不传）」会让人以为还有个「不传」可挑。
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({
      endpoints: [CHAT_ENDPOINT, { ...COMFYUI_VIDEO_ENDPOINT, size_fixed: true, native_resolution: null }],
    });
    renderForm();
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    selectProtocol("comfyui");
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
    selectProtocol("comfyui");
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    const picker = screen.getByLabelText("分辨率");
    expect(picker).toBeEnabled();
    expect(picker).toHaveAttribute("placeholder", "workflow 原生（720p）");
  });

  it("announces the per-protocol concurrency default of one", () => {
    renderForm();
    expect(screen.getByLabelText("视频并发")).toHaveAttribute("placeholder", "默认");

    selectProtocol("comfyui");

    expect(screen.getByLabelText("视频并发")).toHaveAttribute("placeholder", "默认 1");
    expect(screen.getByText(/该协议默认 1/)).toBeInTheDocument();
  });
});
