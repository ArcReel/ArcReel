import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type {
  CustomEndpointInfo,
  CustomProviderInfo,
  CustomProviderModelInfo,
  EndpointDefinition,
} from "@/types";
import imageTemplate from "@/data/example-templates/generic-image-submit-poll.json";
import {
  MINE,
  chooseMenu,
  clickRail,
  makeDefinition,
  pasteSource,
  pickFile,
  renderSection,
  stubEndpointsSection,
  validation,
} from "./endpoints-section-test-utils";

function model(overrides: Partial<CustomProviderModelInfo>): CustomProviderModelInfo {
  return {
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
    max_output_tokens: null,
    system_capabilities: null,
    capability_overrides: null,
    global_bucket_refs: null,
    ...overrides,
  };
}

function provider(overrides: Partial<CustomProviderInfo>): CustomProviderInfo {
  return {
    id: 1,
    display_name: "Relay",
    discovery_format: "openai",
    base_url: "https://api.example.com",
    api_key_masked: "sk-***",
    created_at: "2026-08-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
    models: [model({})],
    ...overrides,
  };
}

describe("EndpointsSection", () => {
  beforeEach(stubEndpointsSection);

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

  it("splits the rail into my endpoints and built-in ones, code-implemented endpoints included", async () => {
    renderSection();
    const list = await screen.findByRole("navigation");
    expect(within(list).getByRole("tab", { name: /我的端点/ })).toBeInTheDocument();
    await userEvent.click(within(list).getByRole("tab", { name: /内置/ }));
    const builtin = within(list).getByRole("tabpanel");
    expect(within(builtin).getByRole("link", { name: /NewAPI Video/ })).toBeInTheDocument();
    expect(within(builtin).getByRole("link", { name: /OpenAI 视频|OpenAI Video/ })).toBeInTheDocument();
  });

  it("selects the first of my endpoints when the address names none", async () => {
    const { location } = renderSection();
    expect(await screen.findByDisplayValue("Example Video API")).toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=ce-7");
  });

  it("leaves built-in image endpoints out", async () => {
    // 本节是自定义端点的管理面；内置图像端点在这里没有可做的事。
    renderSection();
    const list = await screen.findByRole("navigation");
    expect(within(list).queryByText("OpenAI Image")).not.toBeInTheDocument();
  });

  it("opens a non-video built-in endpoint deep link and returns to its provider", async () => {
    vi.mocked(API.listCustomProviders).mockResolvedValue({ providers: [provider({})] });
    const { location } = renderSection("section=endpoints&endpoint=openai-image&from=1");
    expect(await screen.findByText("该端点由代码实现，仅展示接口信息。")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("link", { name: "返回「Relay」" }));
    expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=1");
  });

  it("edits the JSON view in monospace", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    await screen.findByDisplayValue("Example Video API");
    await userEvent.click(screen.getByRole("button", { name: "JSON" }));

    expect(screen.getByRole("textbox", { name: "JSON" })).toHaveClass("font-mono");
  });

  it.each(["{", '{"meta": {}}'])("keeps invalid JSON edits in the leave guard and discards the raw text: %s", async (text) => {
    const update = vi.spyOn(API, "updateCustomEndpoint");
    const { location } = renderSection("section=endpoints&endpoint=ce-7", { guarded: true });
    await screen.findByDisplayValue("Example Video API");
    await userEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByRole("textbox", { name: "JSON" }), { target: { value: text } });
    await clickRail("新建端点");
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "继续编辑" }));
    expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=ce-7");
    expect(screen.getByRole("textbox", { name: "JSON" })).toHaveValue(text);
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("textbox", { name: "JSON" })).toHaveValue(text);
    expect(update).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(JSON.parse((screen.getByRole("textbox", { name: "JSON" }) as HTMLTextAreaElement).value)).toEqual(MINE.definition);
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("shows an editable lifecycle form for one of my endpoints", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByDisplayValue("Example Video API")).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByText("提交生成任务")).toBeInTheDocument();
  });

  it("offers the accepted duration variable without the rejected duration_seconds alias", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByDisplayValue("Example Video API")).toBeEnabled();
    expect(screen.getByText("{{ duration }}")).toBeInTheDocument();
    expect(screen.queryByText("{{ duration_seconds }}")).not.toBeInTheDocument();
  });

  it("prefills a new endpoint from the image example template and saves it as an image definition", async () => {
    const create = vi.spyOn(API, "createCustomEndpoint").mockResolvedValue(MINE);
    renderSection("section=endpoints&endpoint=new");

    await userEvent.click(await screen.findByRole("combobox", { name: "示例模板" }));
    await userEvent.click(await screen.findByRole("option", { name: "图片：提交 + 轮询" }));

    expect(screen.getByRole("checkbox", { name: "图生图" })).toBeChecked();
    expect(screen.queryByText("视频地址")).not.toBeInTheDocument();
    const save = screen.getByRole("button", { name: "保存" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(create.mock.calls[0][0]).toEqual(imageTemplate);
  });

  it("surfaces validation errors on the diagnostics card and blocks saving", async () => {
    const update = vi.spyOn(API, "updateCustomEndpoint");
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
    // 诊断卡要等 400ms 校验防抖再发请求，默认 1s 等待在并行负载下不够。
    expect(
      await screen.findByText("不支持递归下降语法", undefined, { timeout: 4000 }),
    ).toBeInTheDocument();
    await userEvent.type(screen.getByDisplayValue("Example Video API"), "!");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/存在错误，修正后才能保存。/)).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it("saves an edited definition through the update endpoint", async () => {
    const update = vi
      .spyOn(API, "updateCustomEndpoint")
      .mockResolvedValue({ ...MINE, display_name: "Renamed" });
    renderSection("section=endpoints&endpoint=ce-7");
    const nameField = await screen.findByDisplayValue("Example Video API");
    await userEvent.type(nameField, "!");
    const save = screen.getByRole("button", { name: "保存" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update.mock.calls[0][0]).toBe(7);
    expect((update.mock.calls[0][1] as EndpointDefinition).meta.name).toBe(
      "Example Video API!",
    );
  });

  it("shows server references when deletion conflicts and offers a model-row jump", async () => {
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [provider({})] });
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
    await screen.findByDisplayValue("Example Video API");
    await chooseMenu("删除端点");
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "删除" }));
    const jump = await screen.findByRole("button", { name: "Relay · Example Video — 前往模型行" });
    await userEvent.click(jump);
    expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=1&model=example-video");
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

  it("keeps the import dialog from being cancelled while the definition is being saved", async () => {
    const definition = makeDefinition({ meta: { name: "Pasted API", author: "me", version: "1.0.0" } });
    let finish: (value: CustomEndpointInfo) => void = () => {};
    vi.spyOn(API, "createCustomEndpoint").mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    renderSection();
    await screen.findByRole("navigation");

    await pasteSource(JSON.stringify(definition));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "导入" }));

    expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
    await act(async () => finish(MINE));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers a copy of a built-in declarative endpoint instead of editing it", async () => {
    vi.spyOn(API, "getBuiltinEndpointDefinition").mockResolvedValue(
      makeDefinition({ meta: { name: "NewAPI Video", author: "ArcReel", version: "1.0.0" } }),
    );
    const create = vi.spyOn(API, "createCustomEndpoint").mockResolvedValue(MINE);
    renderSection("section=endpoints&endpoint=newapi-video");

    expect(await screen.findByDisplayValue("NewAPI Video")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "JSON" }));
    expect(screen.getByRole("textbox", { name: "JSON" })).toHaveAttribute("readonly");
    await userEvent.click(screen.getByRole("button", { name: "复制为我的端点" }));
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
  });

  it("puts creating a provider and copying a built-in endpoint side by side in its header", async () => {
    vi.spyOn(API, "getBuiltinEndpointDefinition").mockResolvedValue(
      makeDefinition({ meta: { name: "NewAPI Video", author: "ArcReel", version: "1.0.0" } }),
    );
    const { location } = renderSection("section=endpoints&endpoint=newapi-video");

    await screen.findByDisplayValue("NewAPI Video");
    expect(screen.getByRole("button", { name: "复制为我的端点" })).toBeEnabled();
    await userEvent.click(await screen.findByRole("button", { name: "新建供应商并使用" }));
    expect(location.history.at(-1)).toMatch(/^\/app\/settings\?section=providers&custom=new&endpoint=newapi-video/);
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

  it("lists the models using the endpoint and opens each one in its provider", async () => {
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({
      providers: [
        provider({}),
        provider({
          id: 2,
          display_name: "Backup relay",
          models: [model({ id: 21, model_id: "backup-video" }), model({ id: 22, model_id: "other", endpoint: "newapi-video" })],
        }),
      ],
    });
    const { location } = renderSection("section=endpoints&endpoint=ce-7");

    const usage = await screen.findByRole("region", { name: "使用这个端点的模型" });
    const rows = within(usage).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("example-video"),
      expect.stringContaining("backup-video"),
    ]);
    expect(within(rows[1]).getByText("Backup relay")).toBeInTheDocument();
    // 二级栏第二行写的是同一份数据
    expect(screen.getAllByRole("link", { name: /Example Video API.*2 个模型使用/ })[0]).toBeInTheDocument();

    await userEvent.click(within(rows[1]).getByRole("link", { name: "打开「Backup relay」中的模型 backup-video" }));
    expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=2&model=backup-video");
  });

  it("says so when no model uses the endpoint", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    const usage = await screen.findByRole("region", { name: "使用这个端点的模型" });
    expect(within(usage).getByText("还没有模型使用这个端点。")).toBeInTheDocument();
  });

  it("leads back to the provider it was opened from, across endpoint switches", async () => {
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [provider({})] });
    const { location } = renderSection("section=endpoints&endpoint=ce-7&from=1");

    expect(await screen.findByRole("link", { name: "返回「Relay」" })).toHaveAttribute(
      "href",
      "/app/settings?section=providers&custom=1",
    );
    await clickRail(/NewAPI Video/);
    expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=newapi-video&from=1");
    await userEvent.click(await screen.findByRole("link", { name: "返回「Relay」" }));
    expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=1");
  });

  it("asks before switching away from an endpoint with unsaved edits", async () => {
    const { location } = renderSection("section=endpoints&endpoint=ce-7", { guarded: true });
    await userEvent.type(await screen.findByDisplayValue("Example Video API"), "!");

    await clickRail(/NewAPI Video/);

    expect(await screen.findByRole("alertdialog", { name: "有未保存的修改" })).toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=ce-7");
  });

  it("shows only the request details for an endpoint implemented in code", async () => {
    renderSection("section=endpoints&endpoint=openai_video");
    expect(await screen.findByText("该端点由代码实现，仅展示接口信息。")).toBeInTheDocument();
    expect(screen.getByText("/v1/videos")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制为我的端点" })).not.toBeInTheDocument();
  });
});
