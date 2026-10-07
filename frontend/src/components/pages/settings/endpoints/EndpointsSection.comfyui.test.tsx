import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  ComfyuiBindingTarget,
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  ComfyuiMatchOrigin,
  CustomEndpointInfo,
} from "@/types";
import {
  CATALOG,
  COMFYUI_MINE,
  MINE,
  captureDownloads,
  chooseMenu,
  clickRail,
  descriptor,
  pasteSource,
  pickFile,
  renderSection,
  stubEndpointsSection,
  validation,
} from "./endpoints-section-test-utils";

describe("EndpointsSection", () => {
  beforeEach(stubEndpointsSection);

  describe("ComfyUI endpoints", () => {
    const PROMPT_TARGET = { node: "6", input: "text", class_type: "CLIPTextEncode" };
    const OUTPUT_TARGET = { node: "9", class_type: "SaveVideo" };

    /** 「重新导入」直接在已打开的端点弹窗里换文件，不经过 pickFile 的打开步骤，所以单独取 file input。 */
    function importFileInput(): HTMLInputElement {
      const picker = screen.getByRole("dialog").querySelector<HTMLInputElement>('input[type="file"]');
      if (picker === null) throw new Error("no file input");
      return picker;
    }

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

    it("lists workflow endpoints of both media types among my endpoints", async () => {
      renderSection();
      const mine = within(await screen.findByRole("navigation")).getByRole("tabpanel");

      expect(within(mine).getByRole("link", { name: /我的 ComfyUI/ })).toBeInTheDocument();
      expect(within(mine).getByRole("link", { name: /我的画图 workflow/ })).toBeInTheDocument();
      expect(within(mine).getByRole("link", { name: /Example Video API/ })).toBeInTheDocument();
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

      await screen.findByLabelText("端点名称");
      await chooseMenu("删除端点");
      await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "删除" }));

      await waitFor(() => expect(remove).toHaveBeenCalledWith(8));
    });

    it("keeps the export action so a saved definition can still be backed up", async () => {
      // 端点定义不含凭证，导出即备份与分享的那一步。导出的是编辑器里这一刻的定义：workflow
      // 与当前的节点绑定一并在内，包括还没保存的那几条。
      const downloads = captureDownloads();
      renderSection("section=endpoints&endpoint=ce-8");
      await screen.findByLabelText("端点名称");

      await chooseMenu("导出 JSON");

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
      expect(screen.getByText(/先给这份 workflow 起个名字/)).toBeInTheDocument();
    });

    it("falls back to the first endpoint after discarding a newly imported workflow", async () => {
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
      const { location } = renderSection();
      await screen.findByRole("navigation");

      await pickFile(new File([JSON.stringify(workflow)], "workflow_api.json", { type: "application/json" }));
      await userEvent.click(await screen.findByRole("button", { name: "去绑定节点" }));
      expect(await screen.findByLabelText("端点名称")).toHaveValue("ComfyUI workflow");
      await userEvent.click(screen.getByRole("button", { name: "放弃修改" }));

      await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=ce-7"));
      expect(await screen.findByDisplayValue("Example Video API")).toBeInTheDocument();
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
      fireEvent.change(importFileInput(), {
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
      fireEvent.change(importFileInput(), {
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
        fireEvent.change(importFileInput(), {
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
      await clickRail(/我的画图 workflow/);
      await screen.findByLabelText("端点名称");
      await userEvent.click(screen.getByRole("button", { name: "重新导入" }));
      await pickFile("v3_api.json");

      // excludeId 取的就是这份草稿背着的 record.id，它也是保存时会被写回的那一行。
      expect(validate.mock.calls.at(-1)?.[1]).toMatchObject({ excludeId: 9 });
    });

    it("asks before switching away from a workflow endpoint with unsaved edits", async () => {
      const { location } = renderSection("section=endpoints&endpoint=ce-8", { guarded: true });
      await screen.findByText("1 个节点");
      await userEvent.type(screen.getByLabelText("端点名称"), "!");

      await clickRail(/Example Video API/);

      expect(await screen.findByRole("alertdialog", { name: "有未保存的修改" })).toBeInTheDocument();
      expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=ce-8");
    });

    it("still shows the declarative form for my declarative endpoint", async () => {
      renderSection("section=endpoints&endpoint=ce-7");

      expect(await screen.findByDisplayValue("Example Video API")).toBeEnabled();
      expect(screen.queryByLabelText("端点名称")).not.toBeInTheDocument();
    });
  });
});
