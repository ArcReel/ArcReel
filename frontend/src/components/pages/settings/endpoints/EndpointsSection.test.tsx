import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  ComfyuiBindingTarget,
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  ComfyuiMatchOrigin,
  CustomEndpointInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointInstallation,
  EndpointValidateResponse,
  MarketEntry,
} from "@/types";
import { MARKET_CONTRIBUTING_URL } from "../market/market-links";
import { EndpointsSection } from "./EndpointsSection";

function makeDefinition(overrides?: Partial<EndpointDefinition>): EndpointDefinition {
  return {
    kind: "declarative",
    schema_version: "1.0.0",
    meta: { name: "Example Video API", author: "Ada", version: "1.0.0" },
    auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
    submit: {
      method: "POST",
      url: "{{ base_url }}/v1/videos",
      body: { model: "{{ model }}" },
      extract: { task_id: ["$.id"] },
    },
    poll: {
      method: "GET",
      url: "{{ base_url }}/v1/videos/{{ task_id }}",
      extract: { status: ["$.status"], video_url: ["$.data.video_url"] },
    },
    status_map: { succeeded: "succeeded" },
    ...overrides,
  };
}

const MINE: CustomEndpointInfo = {
  installation: null,
  id: 7,
  key: "ce-7",
  display_name: "Example Video API",
  kind: "declarative",
  schema_version: "1.0.0",
  media_type: "video",
  definition: makeDefinition(),
  created_at: null,
  updated_at: null,
};

const COMFYUI_MINE: CustomEndpointInfo = {
  installation: null,
  id: 8,
  key: "ce-8",
  display_name: "我的 ComfyUI",
  kind: "comfyui",
  schema_version: "1.0.0",
  media_type: "video",
  definition: {
    kind: "comfyui",
    schema_version: "1.0.0",
    meta: { name: "我的 ComfyUI", author: "Ada", version: "1.0.0" },
    media_type: "video",
    workflow: { "9": { class_type: "SaveVideo", inputs: {} } },
    bindings: { prompt: [{ node: "6", input: "text", class_type: "CLIPTextEncode" }] },
  },
  created_at: null,
  updated_at: null,
};

function descriptor(overrides: Partial<EndpointDescriptor>): EndpointDescriptor {
  return {
    key: "ce-7",
    media_type: "video",
    family: "custom",
    kind: "declarative",
    source: "custom",
    display_name_key: "",
    display_name: "Example Video API",
    request_method: "POST",
    request_path_template: "/v1/videos",
    image_capabilities: null,
    end_image_capable: false,
    size_fixed: false,
    duration_fixed: false,
    duration_tier_empty: false,
    native_resolution: null,
    ...overrides,
  };
}

const CATALOG: EndpointDescriptor[] = [
  descriptor({}),
  descriptor({
    key: "newapi-video",
    family: "newapi",
    source: "builtin",
    display_name: "NewAPI Video",
  }),
  descriptor({
    key: "openai_video",
    family: "openai",
    kind: "python",
    source: "builtin",
    display_name: null,
    display_name_key: "endpoint_openai_video_display",
  }),
  descriptor({
    key: "openai-image",
    media_type: "image",
    source: "builtin",
    display_name: "OpenAI Image",
  }),
];

function validation(overrides?: Partial<EndpointValidateResponse>): EndpointValidateResponse {
  return {
    errors: [],
    warnings: [],
    duplicates: [],
    hints: null,
    schema_version: { file: "1.0.0", current: "1.0.0", level: "direct" },
    min_app_version: null,
    import_shape: "endpoint_definition",
    wrapped_definition: null,
    ...overrides,
  };
}

function renderSection(search = "section=endpoints") {
  const location = memoryLocation({ path: "/app/settings", searchPath: search, record: true });
  return {
    ...render(
      <Router hook={location.hook}>
        <EndpointsSection />
      </Router>,
    ),
    location,
  };
}

/** 在导入弹窗里选一份文件。隐藏的 file input 在 jsdom 里只能这样驱动。 */
async function pickFile(file: File) {
  await userEvent.click(screen.getByRole("button", { name: "导入" }));
  const picker = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (picker === null) throw new Error("no file input");
  fireEvent.change(picker, { target: { files: [file] } });
}

/** 在导入弹窗里粘贴一份载荷，与上传走同一条分流。 */
async function pasteSource(text: string) {
  await userEvent.click(screen.getByRole("button", { name: "导入" }));
  await userEvent.click(screen.getByLabelText("粘贴端点定义或 workflow"));
  await userEvent.paste(text);
  await userEvent.click(screen.getByRole("button", { name: "识别" }));
}

function captureDownloads() {
  const downloads: { name: string; blob: Blob }[] = [];
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn((blob: Blob) => {
      downloads.push({ name: "", blob });
      return "blob:definition";
    }),
  });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    downloads[downloads.length - 1].name = this.download;
  });
  return downloads;
}

describe("EndpointsSection", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useEndpointCatalogStore.setState({
      endpoints: CATALOG,
      loading: false,
      initialized: true,
    });
    vi.restoreAllMocks();
    vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({ endpoints: [MINE] });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(useEndpointCatalogStore.getState(), "refresh").mockResolvedValue(undefined);
    vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(validation());
  });

  it("carries the server's wrapped definition when the picked file is a raw ComfyUI workflow", async () => {
    const workflow = { "9": { class_type: "SaveVideo", inputs: { fps: 16 } } };
    const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
      validation({
        import_shape: "comfyui_api_workflow",
        wrapped_definition: {
          kind: "comfyui",
          schema_version: "1.0.0",
          meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
          media_type: "video",
          workflow,
          bindings: {},
        },
        errors: [
          { path: "bindings.prompt", code: "comfyui_binding_required", message: "语义键 prompt 必须绑定到节点后才能保存" },
        ],
      }),
    );
    renderSection();
    await screen.findByRole("navigation");

    await pickFile(new File([JSON.stringify(workflow)], "workflow_api.json", { type: "application/json" }));

    // 原始 workflow 没有 kind，送去校验的是它本身；回来的包装结果接手成为待保存的定义。
    expect(await screen.findByText(/已包装成 ComfyUI 端点定义/)).toBeInTheDocument();
    expect(validate.mock.calls[0][0]).toEqual(workflow);
    expect(screen.getByText(/workflow_api\.json · ComfyUI workflow · v1\.0\.0/)).toBeInTheDocument();
  });

  it("groups endpoints by whether they are mine, built-in, or implemented in code", async () => {
    renderSection();
    const list = await screen.findByRole("navigation");
    expect(within(list).getByText("我的端点")).toBeInTheDocument();
    expect(within(list).getByText("内置")).toBeInTheDocument();
    expect(within(list).getByText("内置 · Python")).toBeInTheDocument();
  });

  it("leaves built-in image endpoints out", async () => {
    // 本节是自定义端点的管理面；内置图像端点在这里没有可做的事。
    renderSection();
    const list = await screen.findByRole("navigation");
    expect(within(list).queryByText("OpenAI Image")).not.toBeInTheDocument();
  });

  it("rejects a built-in image endpoint selected through the URL", async () => {
    renderSection("section=endpoints&endpoint=openai-image");
    expect(await screen.findByText("选择一个端点查看其定义。")).toBeInTheDocument();
    expect(screen.queryByText("该端点由代码实现，仅展示接口信息。")).not.toBeInTheDocument();
  });

  it("shows an editable lifecycle form for one of my endpoints", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByDisplayValue("Example Video API")).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存更改" })).toBeInTheDocument();
    expect(screen.getByText("提交生成任务")).toBeInTheDocument();
  });

  it("surfaces validation errors on the diagnostics card and blocks saving", async () => {
    vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
      validation({
        errors: [
          {
            path: "poll.extract.video_url[0]",
            code: "jsonpath_recursive_descent",
            message: "不支持递归下降语法",
          },
        ],
      }),
    );
    renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByText("不支持递归下降语法")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled(),
    );
  });

  it("saves an edited definition through the update endpoint", async () => {
    const update = vi
      .spyOn(API, "updateCustomEndpoint")
      .mockResolvedValue({ ...MINE, display_name: "Renamed" });
    renderSection("section=endpoints&endpoint=ce-7");
    const nameField = await screen.findByDisplayValue("Example Video API");
    await userEvent.type(nameField, "!");
    const save = screen.getByRole("button", { name: "保存更改" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update.mock.calls[0][0]).toBe(7);
    expect((update.mock.calls[0][1] as EndpointDefinition).meta.name).toBe(
      "Example Video API!",
    );
  });

  it("shows server references when deletion conflicts and offers a model-row jump", async () => {
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({
      providers: [
        {
          id: 1,
          display_name: "Relay",
          discovery_format: "openai",
          base_url: "https://api.example.com",
          api_key_masked: "sk-***",
          created_at: "2026-08-01T00:00:00Z",
          image_max_workers: null,
          video_max_workers: null,
          audio_max_workers: null,
          models: [
            {
              id: 11,
              model_id: "example-video",
              display_name: "example-video",
              endpoint: "ce-7",
              is_default: true,
              is_enabled: true,
              price_unit: null,
              price_input: null,
              price_output: null,
              currency: null,
              supported_durations: null,
              resolution: null,
              system_capabilities: null,
              capability_overrides: null,
              global_bucket_refs: null,
            },
          ],
        },
      ],
    });
    vi.spyOn(API, "deleteCustomEndpoint").mockRejectedValue(
      new ApiRequestError(
        "Models are using this endpoint.",
        {
          references: [
            {
              provider_id: 1,
              provider_display_name: "Relay",
              model_id: "example-video",
              model_display_name: "Example Video",
            },
          ],
        },
        409,
      ),
    );
    const { location } = renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByText("1 个模型正在使用")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await userEvent.click(screen.getAllByRole("button", { name: "删除" }).at(-1)!);
    const jump = await screen.findByRole("button", { name: "Relay · Example Video — 前往模型行" });
    await userEvent.click(jump);
    expect(location.history.at(-1)).toBe(
      "/app/settings?section=providers&custom=1&model=example-video",
    );
  });

  it("leaves the dialog standing when the pasted text is not JSON, so it can be fixed in place", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(validation());
    renderSection();
    await screen.findByRole("navigation");

    await pasteSource("{ 这不是 JSON");

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "内容不是有效的 JSON 定义。请选择从端点导出的定义文件，或粘贴一份完整的 JSON。",
        "error",
      ),
    );
    expect(validate).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("imports a declarative definition pasted into the dialog, not just an uploaded file", async () => {
    const definition = makeDefinition({ meta: { name: "Pasted API", author: "me", version: "1.0.0" } });
    const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(validation());
    const create = vi.spyOn(API, "createCustomEndpoint").mockResolvedValue(MINE);
    renderSection();
    await screen.findByRole("navigation");

    await pasteSource(JSON.stringify(definition));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "导入" }));

    expect(validate.mock.calls[0][0]).toEqual(definition);
    await waitFor(() => expect(create).toHaveBeenCalledWith(definition));
  });

  it("offers a copy of a built-in declarative endpoint instead of editing it", async () => {
    vi.spyOn(API, "getBuiltinEndpointDefinition").mockResolvedValue(
      makeDefinition({ meta: { name: "NewAPI Video", author: "ArcReel", version: "1.0.0" } }),
    );
    const create = vi.spyOn(API, "createCustomEndpoint").mockResolvedValue(MINE);
    renderSection("section=endpoints&endpoint=newapi-video");

    expect(await screen.findByDisplayValue("NewAPI Video")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "保存更改" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "JSON" }));
    expect(screen.getByRole("textbox", { name: "JSON" })).toHaveClass(
      "read-only:border-accent/25",
      "read-only:bg-bg-grad-b/65",
      "read-only:text-text-2",
    );
    await userEvent.click(screen.getByRole("button", { name: "复制为我的" }));
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
  });

  it("keeps focus in a key field while its name is being typed", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    const nameField = await screen.findByLabelText("请求头名称");
    await userEvent.clear(nameField);
    await userEvent.type(nameField, "X-Token");
    expect(screen.getByLabelText("请求头名称")).toHaveFocus();
    expect(screen.getByLabelText("请求头名称")).toHaveValue("X-Token");
  });

  it("rejects a duplicate key with a toast without overwriting either row", async () => {
    vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({
      endpoints: [
        {
          ...MINE,
          definition: makeDefinition({
            auth: {
              headers: {
                Authorization: "Bearer {{ api_key }}",
                "X-Token": "abc",
              },
            },
          }),
        },
      ],
    });
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    renderSection("section=endpoints&endpoint=ce-7");
    const [nameField] = await screen.findAllByLabelText("请求头名称");

    fireEvent.change(nameField, { target: { value: "X-Token" } });

    expect(screen.getAllByLabelText("请求头名称").map((field) => field.getAttribute("value"))).toEqual([
      "Authorization",
      "X-Token",
    ]);
    expect(screen.getAllByLabelText("请求头内容").map((field) => field.getAttribute("value"))).toEqual([
      "Bearer {{ api_key }}",
      "abc",
    ]);
    expect(pushToast).toHaveBeenCalledWith("该名称已被使用，请换一个名称。", "error");
  });

  it("asks for the new row to be named before another one can be added", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    const add = await screen.findByRole("button", { name: "添加请求头" });
    await userEvent.click(add);
    expect(screen.getAllByLabelText("请求头名称")).toHaveLength(2);
    expect(add).toBeDisabled();
    expect(screen.getByText("先为新增的这一行填写名称，再添加下一行。")).toBeInTheDocument();
  });

  describe("ComfyUI endpoints", () => {
    const PROMPT_TARGET = { node: "6", input: "text", class_type: "CLIPTextEncode" };
    const OUTPUT_TARGET = { node: "9", class_type: "SaveVideo" };

    function keyInference(target: ComfyuiBindingTarget, origin: ComfyuiMatchOrigin = "inferred") {
      return {
        state: "auto_selected" as const,
        candidates: [{ target, score: 200, signals: [], selected: true, origin, depth: null }],
        notes: [],
      };
    }

    function inference(overrides?: Partial<ComfyuiInferResponse>): ComfyuiInferResponse {
      return {
        media_type: "video",
        savable: true,
        bindings: { prompt: keyInference(PROMPT_TARGET), output: keyInference(OUTPUT_TARGET) },
        notes: [],
        import_shape: "comfyui_api_workflow",
        wrapped_definition: null,
        ...overrides,
      };
    }

    const IMAGE_MINE: CustomEndpointInfo = {
      ...COMFYUI_MINE,
      id: 9,
      key: "ce-9",
      media_type: "image",
      display_name: "我的画图 workflow",
    };

    beforeEach(() => {
      useEndpointCatalogStore.setState({
        endpoints: [
          ...CATALOG,
          descriptor({ key: "ce-8", kind: "comfyui", display_name: "我的 ComfyUI" }),
          descriptor({ key: "ce-9", kind: "comfyui", media_type: "image", display_name: "我的画图 workflow" }),
        ],
        loading: false,
        initialized: true,
      });
      vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({ endpoints: [MINE, COMFYUI_MINE, IMAGE_MINE] });
      vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(inference());
    });

    it("gives workflow endpoints a group of their own and mixes both media types into it", async () => {
      renderSection();
      const list = await screen.findByRole("navigation");

      expect(within(list).getByText("ComfyUI workflow")).toBeInTheDocument();
      const video = within(list).getByRole("button", { name: /我的 ComfyUI/ });
      const image = within(list).getByRole("button", { name: /我的画图 workflow/ });
      expect(within(video).getByText("视频")).toBeInTheDocument();
      expect(within(image).getByText("图片")).toBeInTheDocument();
      // 声明式端点留在「我的端点」里，不跟着 workflow 走。
      expect(within(list).getByRole("button", { name: /Example Video API/ })).toBeInTheDocument();
    });

    it("opens a saved workflow endpoint in the binding editor", async () => {
      const infer = vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(inference());
      renderSection("section=endpoints&endpoint=ce-8");

      expect(await screen.findByLabelText("端点名称")).toHaveValue("我的 ComfyUI");
      // 服务端不留状态：进详情拿当前这份定义重跑一次，它已确认的节点绑定即重匹配的输入。
      await waitFor(() => expect(infer).toHaveBeenCalledOnce());
      expect(infer.mock.calls[0][0]).toEqual(COMFYUI_MINE.definition);
      expect(await screen.findByText("1 个节点")).toBeInTheDocument();
      expect(screen.queryByText("提交生成任务")).not.toBeInTheDocument();
    });

    it("keeps the delete action so an imported endpoint can still be removed", async () => {
      const remove = vi.spyOn(API, "deleteCustomEndpoint").mockResolvedValue(undefined);
      renderSection("section=endpoints&endpoint=ce-8");

      await userEvent.click(await screen.findByRole("button", { name: "删除" }));
      await userEvent.click(screen.getAllByRole("button", { name: "删除" }).at(-1)!);

      await waitFor(() => expect(remove).toHaveBeenCalledWith(8));
    });

    it("keeps the export action so a saved definition can still be backed up", async () => {
      // 端点定义不含凭证，导出即备份与分享的那一步。导出的是编辑器里这一刻的定义：workflow
      // 与当前的节点绑定一并在内，包括还没保存的那几条。
      const downloads = captureDownloads();
      renderSection("section=endpoints&endpoint=ce-8");
      await screen.findByLabelText("端点名称");

      await userEvent.click(screen.getByRole("button", { name: "导出定义" }));

      expect(downloads).toHaveLength(1);
      expect(downloads[0].name).toBe("comfyui.json");
      expect(JSON.parse(await downloads[0].blob.text())).toEqual({
        ...COMFYUI_MINE.definition,
        bindings: { prompt: [PROMPT_TARGET], output: [OUTPUT_TARGET] },
      });
    });

    it("takes a raw workflow from the import dialog into the binding editor instead of saving it", async () => {
      const workflow = { "9": { class_type: "SaveVideo", inputs: { fps: 16 } } };
      const wrapped: ComfyuiEndpointDefinition = {
        kind: "comfyui",
        schema_version: "1.0.0",
        meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
        media_type: "video",
        workflow,
        bindings: {},
      };
      vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({ import_shape: "comfyui_api_workflow", wrapped_definition: wrapped }),
      );
      const infer = vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(inference());
      const create = vi.spyOn(API, "createCustomEndpoint");
      renderSection();
      await screen.findByRole("navigation");

      await pickFile(new File([JSON.stringify(workflow)], "workflow_api.json", { type: "application/json" }));
      await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));

      expect(infer).toHaveBeenCalledWith(wrapped, { mediaType: "video" });
      expect(await screen.findByLabelText("端点名称")).toHaveValue("ComfyUI workflow");
      expect(screen.queryByRole("button", { name: "去绑定节点" })).not.toBeInTheDocument();
      expect(create).not.toHaveBeenCalled();
      // 占位名要先改掉：同作者同名的两份 workflow 会被判成同一份。
      expect(screen.getByRole("button", { name: "保存端点" })).toBeDisabled();
      expect(screen.getByText(/先给这份 workflow 起个名字/)).toBeInTheDocument();
    });

    it("drops an inference that comes back after the dialog was dismissed", async () => {
      // 推断在途时取消：迟到的那一份会把一个已经被放弃的 workflow 装进详情并跳过去。
      const workflow = { "9": { class_type: "SaveVideo", inputs: { fps: 16 } } };
      const wrapped: ComfyuiEndpointDefinition = {
        kind: "comfyui",
        schema_version: "1.0.0",
        meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
        media_type: "video",
        workflow,
        bindings: {},
      };
      vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({ import_shape: "comfyui_api_workflow", wrapped_definition: wrapped }),
      );
      let release: (value: ComfyuiInferResponse) => void = () => {};
      const pending = new Promise<ComfyuiInferResponse>((resolve) => {
        release = resolve;
      });
      vi.spyOn(API, "inferComfyuiBindings").mockReturnValue(pending);
      renderSection();
      await screen.findByRole("navigation");

      await pasteSource(JSON.stringify(workflow));
      await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));
      await userEvent.click(screen.getByRole("button", { name: "取消" }));
      release(inference());
      await act(async () => {
        await pending;
      });

      expect(screen.queryByLabelText("端点名称")).not.toBeInTheDocument();
    });

    it("takes a workflow pasted into the dialog down the same path as an uploaded one", async () => {
      const workflow = { "9": { class_type: "SaveVideo", inputs: { fps: 16 } } };
      const wrapped: ComfyuiEndpointDefinition = {
        kind: "comfyui",
        schema_version: "1.0.0",
        meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
        media_type: "video",
        workflow,
        bindings: {},
      };
      const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({ import_shape: "comfyui_api_workflow", wrapped_definition: wrapped }),
      );
      const infer = vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(inference());
      renderSection();
      await screen.findByRole("navigation");

      await pasteSource(JSON.stringify(workflow));
      await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));

      expect(validate.mock.calls[0][0]).toEqual(workflow);
      expect(infer).toHaveBeenCalledWith(wrapped, { mediaType: "video" });
      expect(await screen.findByLabelText("端点名称")).toHaveValue("ComfyUI workflow");
      // 粘贴进来的没有文件名，头部的「来源文件」因此不显示。
      expect(screen.queryByText(/^来自 /)).not.toBeInTheDocument();
    });

    it("asks a raw workflow what it produces and re-wraps it under the answer", async () => {
      const workflow = { "9": { class_type: "SaveImage", inputs: {} } };
      const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({ import_shape: "comfyui_api_workflow", wrapped_definition: null }),
      );
      renderSection();
      await screen.findByRole("navigation");

      await pickFile(new File([JSON.stringify(workflow)], "workflow_api.json", { type: "application/json" }));
      await userEvent.click(await screen.findByRole("button", { name: "图片" }));

      await waitFor(() => expect(validate).toHaveBeenCalledTimes(2));
      expect(validate.mock.calls[0][1]).toMatchObject({ mediaType: "video" });
      expect(validate.mock.calls[1][1]).toMatchObject({ mediaType: "image" });
      expect(validate.mock.calls[1][0]).toEqual(workflow);
    });

    it("lands a re-imported workflow on the endpoint it was started from, identity and bindings intact", async () => {
      const workflow = { "12": { class_type: "SaveVideo", inputs: {} } };
      const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({
          import_shape: "comfyui_api_workflow",
          wrapped_definition: {
            kind: "comfyui",
            schema_version: "1.0.0",
            meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
            media_type: "video",
            workflow,
            bindings: {},
          },
        }),
      );
      const infer = vi
        .spyOn(API, "inferComfyuiBindings")
        .mockResolvedValue(inference({ bindings: { prompt: keyInference(PROMPT_TARGET, "kept") } }));
      renderSection("section=endpoints&endpoint=ce-8");
      await screen.findByLabelText("端点名称");

      await userEvent.click(screen.getByRole("button", { name: "重新导入" }));
      const picker = document.querySelector<HTMLInputElement>('input[type="file"]');
      if (picker === null) throw new Error("no file input");
      fireEvent.change(picker, {
        target: { files: [new File([JSON.stringify(workflow)], "v2_api.json", { type: "application/json" })] },
      });
      await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));

      // 重匹配的输入是「新 workflow 加它原来那份节点绑定」，身份与媒体类型一并沿用。
      await waitFor(() => expect(infer).toHaveBeenCalledTimes(2));
      expect(infer.mock.calls[1][0]).toEqual({ ...COMFYUI_MINE.definition, workflow });
      // 判重时要把这个端点自己排除掉，不然它跟自己同名。
      expect(validate.mock.calls[0][1]).toMatchObject({ excludeId: 8 });
      expect(await screen.findByText("来自 v2_api.json")).toBeInTheDocument();
      expect(screen.getByLabelText("端点名称")).toHaveValue("我的 ComfyUI");
    });

    it("puts the re-imported workflow on screen instead of the one it replaced", async () => {
      // 详情把定义收在自己的 state 里，只在挂载那一刻取自 props：重新导入不换实例的话，来源
      // 文件名换了、屏幕上的 workflow 还是旧的，保存下去的也是旧的。
      const workflow = { "12": { class_type: "VHS_VideoCombine", inputs: {} } };
      vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({
          import_shape: "comfyui_api_workflow",
          wrapped_definition: {
            kind: "comfyui",
            schema_version: "1.0.0",
            meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
            media_type: "video",
            workflow,
            bindings: {},
          },
        }),
      );
      vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(
        inference({ bindings: { prompt: keyInference(PROMPT_TARGET, "kept") } }),
      );
      renderSection("section=endpoints&endpoint=ce-8");
      await screen.findByLabelText("端点名称");
      expect(screen.getByText(/SaveVideo/, { selector: "span" })).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "重新导入" }));
      const picker = document.querySelector<HTMLInputElement>('input[type="file"]');
      if (picker === null) throw new Error("no file input");
      fireEvent.change(picker, {
        target: { files: [new File([JSON.stringify(workflow)], "v2_api.json", { type: "application/json" })] },
      });
      await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));

      expect(await screen.findByText(/VHS_VideoCombine/, { selector: "span" })).toBeInTheDocument();
      expect(screen.queryByText(/SaveVideo/, { selector: "span" })).not.toBeInTheDocument();
    });

    it("re-imports onto the endpoint the user is looking at, not the one a stale draft came from", async () => {
      // 手上留着端点 A 的未保存草稿、人却走到端点 B 上点重新导入时，沿用 A 的身份会把 B 的
      // workflow 存到 A 身上——那是一次谁都没要求过的覆盖。
      const workflow = { "12": { class_type: "SaveVideo", inputs: {} } };
      const validate = vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
        validation({
          import_shape: "comfyui_api_workflow",
          wrapped_definition: {
            kind: "comfyui",
            schema_version: "1.0.0",
            meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
            media_type: "video",
            workflow,
            bindings: {},
          },
        }),
      );
      const pickFile = async (name: string) => {
        const picker = document.querySelector<HTMLInputElement>('input[type="file"]');
        if (picker === null) throw new Error("no file input");
        fireEvent.change(picker, {
          target: { files: [new File([JSON.stringify(workflow)], name, { type: "application/json" })] },
        });
        await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));
      };

      renderSection("section=endpoints&endpoint=ce-8");
      await screen.findByLabelText("端点名称");
      await userEvent.click(screen.getByRole("button", { name: "重新导入" }));
      await pickFile("v2_api.json");
      await screen.findByText("来自 v2_api.json");

      // 不保存这份草稿，直接走到另一个 workflow 端点上再点重新导入。
      const list = await screen.findByRole("navigation");
      await userEvent.click(within(list).getByRole("button", { name: /我的画图 workflow/ }));
      await screen.findByLabelText("端点名称");
      await userEvent.click(screen.getByRole("button", { name: "重新导入" }));
      await pickFile("v3_api.json");

      // excludeId 取的就是这份草稿背着的 record.id，它也是保存时会被写回的那一行。
      expect(validate.mock.calls.at(-1)?.[1]).toMatchObject({ excludeId: 9 });
    });

    it("still shows the declarative form for my declarative endpoint", async () => {
      renderSection("section=endpoints&endpoint=ce-7");

      expect(await screen.findByDisplayValue("Example Video API")).toBeEnabled();
      expect(screen.queryByLabelText("端点名称")).not.toBeInTheDocument();
    });
  });

  it("shows only the request details for an endpoint implemented in code", async () => {
    renderSection("section=endpoints&endpoint=openai_video");
    expect(await screen.findByText("该端点由代码实现，仅展示接口信息。")).toBeInTheDocument();
    expect(screen.getByText("/v1/videos")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制为我的" })).not.toBeInTheDocument();
  });

  describe("market integration", () => {
    const INSTALLATION: EndpointInstallation = {
      source_key: "github:arcreel/arcreel-market@HEAD",
      source_id: 1,
      source_display_name: "ArcReel 官方市场",
      source_enabled: true,
      slug: "kling-master",
      installed_version: "1.0.0",
      installed_at: "2026-09-01T00:00:00+00:00",
      state: "current",
      modified: false,
    };

    function withInstallation(overrides: Partial<EndpointInstallation>) {
      vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({
        endpoints: [{ ...MINE, installation: { ...INSTALLATION, ...overrides } }],
      });
    }

    it("links from the endpoint list to the market section", async () => {
      const { location } = renderSection("section=endpoints&endpoint=ce-7");
      await userEvent.click(await screen.findByRole("button", { name: "从市场获取" }));
      expect(location.history).toEqual(["/app/settings?section=market&endpoint=ce-7"]);
    });

    it("shows both status axes and the source of an installed endpoint without an update action", async () => {
      withInstallation({ modified: true });
      renderSection("section=endpoints&endpoint=ce-7");
      expect(await screen.findByText("来自市场 ArcReel 官方市场")).toBeInTheDocument();
      expect(screen.getByText("自定义")).toBeInTheDocument();
      expect(screen.getByText("已安装")).toBeInTheDocument();
      expect(screen.getByText("已修改")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "更新" })).not.toBeInTheDocument();
    });

    it("leaves a hand-made endpoint without market badges", async () => {
      renderSection("section=endpoints&endpoint=ce-7");
      expect(await screen.findByRole("button", { name: "导出" })).toBeInTheDocument();
      expect(screen.queryByText(/来自市场/)).not.toBeInTheDocument();
      expect(screen.queryByText("已安装")).not.toBeInTheDocument();
    });

    it("names a disabled source and falls back to the canonical key once the source is deleted", async () => {
      withInstallation({ state: "unavailable", source_enabled: false });
      const { unmount } = renderSection("section=endpoints&endpoint=ce-7");
      expect(await screen.findByText("来自市场 ArcReel 官方市场（来源已禁用）")).toBeInTheDocument();
      expect(screen.getByText("市场中不可用")).toBeInTheDocument();
      unmount();

      withInstallation({ state: "unavailable", source_id: null, source_display_name: null, source_enabled: null });
      renderSection("section=endpoints&endpoint=ce-7");
      expect(
        await screen.findByText("来自市场 github:arcreel/arcreel-market@HEAD（来源已删除）"),
      ).toBeInTheDocument();
    });

    it("opens the install dialog in update mode from the update action", async () => {
      withInstallation({ state: "update_available" });
      const entry: MarketEntry = {
        source_id: 1,
        source_display_name: "ArcReel 官方市场",
        type: "endpoint",
        slug: "kling-master",
        path: "endpoints/kling-master/definition.json",
        name: "Example Video API",
        author: "Ada",
        version: "1.1.0",
        media_type: "video",
        description: null,
        homepage: null,
        icon: null,
        min_app_version: null,
        min_app_version_satisfied: true,
        installation: {
          endpoint_id: 7,
          endpoint_key: "ce-7",
          endpoint_display_name: "Example Video API",
          installed_version: "1.0.0",
          state: "update_available",
          modified: false,
        },
      };
      vi.spyOn(API, "getMarketEntry").mockResolvedValue({
        entry,
        source: {
          id: 1,
          kind: "official",
          display_name: "ArcReel 官方市场",
          canonical_key: INSTALLATION.source_key,
          is_enabled: true,
          status: "ok",
          fetched_at: null,
          index: null,
        },
        app_version: null,
      });
      vi.spyOn(API, "getMarketEntryDefinition").mockResolvedValue({
        definition: makeDefinition({ meta: { name: "Example Video API", author: "Ada", version: "1.1.0" } }),
        entry_matches_definition: true,
        definition_digest: "reviewed-digest",
      });
      renderSection("section=endpoints&endpoint=ce-7");

      const actions = (await screen.findByRole("button", { name: "更新" })).parentElement!;
      const labels = within(actions)
        .getAllByRole("button")
        .map((button) => button.textContent);
      expect(labels.indexOf("更新")).toBe(labels.indexOf("新建供应商并使用此端点") + 1);
      expect(labels.indexOf("导出")).toBe(labels.indexOf("更新") + 1);

      await userEvent.type(screen.getByDisplayValue("Example Video API"), "!");
      await userEvent.click(screen.getByRole("button", { name: "更新" }));
      expect(await screen.findByText("Update endpoint")).toBeInTheDocument();
      expect(API.getMarketEntry).toHaveBeenCalledWith(1, "kling-master", expect.anything());
      expect(await screen.findByText("你的本地修改会被覆盖")).toBeInTheDocument();
      const downloads = captureDownloads();
      await userEvent.click(screen.getByRole("button", { name: "先导出当前定义" }));
      expect(JSON.parse(await downloads[0].blob.text()).meta.name).toBe("Example Video API!");
      expect(await screen.findByRole("button", { name: "更新到 v1.1.0" })).toBeInTheDocument();
    });

    it("locks editing and saving while the entry for an update is loading", async () => {
      withInstallation({ state: "update_available" });
      vi.spyOn(API, "getMarketEntry").mockReturnValue(new Promise(() => undefined));
      renderSection("section=endpoints&endpoint=ce-7");

      const nameField = await screen.findByDisplayValue("Example Video API");
      await userEvent.type(nameField, "!");
      const save = screen.getByRole("button", { name: "保存更改" });
      await waitFor(() => expect(save).toBeEnabled());
      await userEvent.click(screen.getByRole("button", { name: "更新" }));

      expect(nameField).toHaveAttribute("readonly");
      expect(save).toBeDisabled();
    });

    it("aborts an entry request when the selected endpoint changes", async () => {
      withInstallation({ state: "update_available" });
      let signal: AbortSignal | undefined;
      vi.spyOn(API, "getMarketEntry").mockImplementation((_sourceId, _slug, options) => {
        signal = options?.signal;
        return new Promise(() => undefined);
      });
      renderSection("section=endpoints&endpoint=ce-7");

      await userEvent.click(await screen.findByRole("button", { name: "更新" }));
      await waitFor(() => expect(signal).toBeDefined());
      await userEvent.click(screen.getByRole("button", { name: "新建" }));
      expect(signal?.aborted).toBe(true);
      expect(screen.queryByText("Update endpoint")).not.toBeInTheDocument();
    });

    it("reports an entry request failure and restores the update action", async () => {
      withInstallation({ state: "update_available" });
      vi.spyOn(API, "getMarketEntry").mockRejectedValue(new Error("entry unavailable"));
      const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
      renderSection("section=endpoints&endpoint=ce-7");

      const update = await screen.findByRole("button", { name: "更新" });
      await userEvent.click(update);
      await waitFor(() => expect(pushToast).toHaveBeenCalledWith("entry unavailable", "error"));
      expect(update).toBeEnabled();
    });

    it("links to the official contribution guide next to export", async () => {
      renderSection("section=endpoints&endpoint=ce-7");
      const link = await screen.findByRole("link", { name: "投稿到市场" });
      expect(link).toHaveAttribute("href", MARKET_CONTRIBUTING_URL);
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noreferrer");
    });

    it("exports an installed endpoint under its market slug with unchanged content", async () => {
      withInstallation({});
      const downloads = captureDownloads();
      renderSection("section=endpoints&endpoint=ce-7");
      await userEvent.click(await screen.findByRole("button", { name: "导出" }));
      expect(downloads).toHaveLength(1);
      expect(downloads[0].name).toBe("kling-master.json");
      expect(await downloads[0].blob.text()).toBe(JSON.stringify(makeDefinition(), null, 2));
    });
  });
});
