import type { ComponentProps } from "react";
import userEvent from "@testing-library/user-event";
import { newEndpointDefinition } from "./endpoint-definition-draft";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ComfyuiEndpointDefinition, EndpointValidateResponse } from "@/types";
import { EndpointImportDialog } from "./EndpointImportDialog";

function validation(overrides?: Partial<EndpointValidateResponse>): EndpointValidateResponse {
  return {
    errors: [],
    warnings: [],
    duplicates: [],
    hints: null,
    schema_version: { file: "1.1.0", current: "1.1.0", level: "direct" },
    min_app_version: null,
    import_shape: "endpoint_definition",
    wrapped_definition: null,
    ...overrides,
  };
}

const COMFYUI_DEFINITION: ComfyuiEndpointDefinition = {
  kind: "comfyui",
  schema_version: "1.0.0",
  meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" },
  media_type: "video",
  workflow: { "6": { class_type: "CLIPTextEncode", inputs: { text: "" } } },
  bindings: {},
};

function renderDialog(
  result: EndpointValidateResponse,
  overrides: Partial<ComponentProps<typeof EndpointImportDialog>> = {},
) {
  render(
    <EndpointImportDialog
      open
      fileName="demo.json"
      definition={null}
      validation={result}
      busy={false}
      pending={false}
      mediaType="video"
      onSource={vi.fn()}
      onMediaTypeChange={vi.fn()}
      onCreateCopy={vi.fn()}
      onOverwrite={vi.fn()}
      onBindNodes={vi.fn()}
      onCancel={vi.fn()}
      {...overrides}
    />,
  );
}

describe("EndpointImportDialog", () => {
  it("preserves immediate overwrite, copy and cancel actions after sharing the choices", async () => {
    const onOverwrite = vi.fn();
    const onCreateCopy = vi.fn();
    const onCancel = vi.fn();
    renderDialog(validation({ duplicates: [{ id: 7, key: "ce-7", display_name: "Demo", version: "1.0.0", relation: "same" }] }), {
      definition: newEndpointDefinition("Demo"),
      onOverwrite,
      onCreateCopy,
      onCancel,
    });
    await userEvent.click(screen.getByRole("button", { name: "覆盖" }));
    expect(onOverwrite).toHaveBeenCalledWith(7);
    await userEvent.click(screen.getByRole("button", { name: "导入为副本" }));
    expect(onCreateCopy).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("tells the user which ArcReel version the definition needs when the app is older", () => {
    renderDialog(validation({ min_app_version: { required: "0.31.0", current: "0.30.0", satisfied: false } }));

    expect(screen.getByText("该定义需要 ArcReel ≥ 0.31.0，当前为 0.30.0，导入后可能无法正常使用。")).toBeInTheDocument();
  });

  it("stays quiet when the app meets the requirement", () => {
    renderDialog(validation({ min_app_version: { required: "0.30.0", current: "0.30.0", satisfied: true } }));

    expect(screen.queryByText(/该定义需要 ArcReel/)).not.toBeInTheDocument();
  });

  it("says a raw ComfyUI workflow was wrapped and still needs its bindings", () => {
    renderDialog(validation({ import_shape: "comfyui_api_workflow" }));

    expect(screen.getByText(/已包装成 ComfyUI 端点定义/)).toBeInTheDocument();
  });

  it("points a UI-format workflow back at the Export (API) menu item", () => {
    renderDialog(
      validation({
        import_shape: "comfyui_ui_workflow",
        errors: [{ path: "$", code: "comfyui_ui_format_workflow", message: "这是 ComfyUI 的 UI 格式 workflow，提交不了" }],
      }),
    );

    expect(screen.getByText(/改用「Export \(API\)」重新导出/)).toBeInTheDocument();
    expect(screen.getByText("文件中的错误修正后才能导入。")).toBeInTheDocument();
  });

  it("keeps the shape notice out of the way for an ordinary endpoint definition", () => {
    renderDialog(validation());

    expect(screen.queryByText(/识别为 ComfyUI 的 API workflow/)).not.toBeInTheDocument();
    expect(screen.queryByText(/改用「Export \(API\)」重新导出/)).not.toBeInTheDocument();
  });

  it("asks a raw workflow what it produces, since that picks the rule set the server infers with", async () => {
    const onMediaTypeChange = vi.fn();
    renderDialog(validation({ import_shape: "comfyui_api_workflow" }), { onMediaTypeChange });

    expect(screen.getByText("这份 workflow 产出")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "video" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "image" }));

    expect(onMediaTypeChange).toHaveBeenCalledWith("image");
  });

  it("leaves the media question out for a definition that already answers it", () => {
    renderDialog(validation({ import_shape: "endpoint_definition" }), { definition: COMFYUI_DEFINITION });

    expect(screen.queryByText("这份 workflow 产出")).not.toBeInTheDocument();
  });

  it("does not hold the wrapped workflow's empty bindings against it, since filling them is the next step", () => {
    renderDialog(
      validation({
        import_shape: "comfyui_api_workflow",
        errors: [{ path: "$.bindings", code: "comfyui_binding_required", message: "prompt 与 output 必须绑定" }],
      }),
      { definition: COMFYUI_DEFINITION },
    );

    expect(screen.queryByText("prompt 与 output 必须绑定")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "去绑定节点" })).toBeEnabled();
  });

  it("takes a pasted raw workflow through the same recognition as an uploaded file", async () => {
    const onSource = vi.fn();
    const workflow = JSON.stringify({ "6": { class_type: "CLIPTextEncode", inputs: { text: "" } } });
    renderDialog(validation({ import_shape: "comfyui_api_workflow" }), { onSource });

    await userEvent.click(screen.getByLabelText("粘贴端点定义或 workflow"));
    await userEvent.paste(workflow);
    await userEvent.click(screen.getByRole("button", { name: "识别" }));

    // 粘进来的没有文件名——详情头部的「来源文件」因此不显示。
    expect(onSource).toHaveBeenCalledWith(workflow, "");
  });

  it("takes a pasted endpoint definition down the same path, ComfyUI or declarative", async () => {
    const onSource = vi.fn();
    const declarative = JSON.stringify(newEndpointDefinition("Demo"));
    renderDialog(validation({ import_shape: "endpoint_definition" }), { onSource });

    await userEvent.click(screen.getByLabelText("粘贴端点定义或 workflow"));
    await userEvent.paste(declarative);
    await userEvent.click(screen.getByRole("button", { name: "识别" }));

    expect(onSource).toHaveBeenCalledWith(declarative, "");
  });

  it("holds recognition back until there is something to recognize", () => {
    renderDialog(validation());

    expect(screen.getByRole("button", { name: "识别" })).toBeDisabled();
  });

  it("hands over an uploaded file with its name and leaves its content in the box to edit", async () => {
    const onSource = vi.fn();
    const workflow = JSON.stringify({ "6": { class_type: "CLIPTextEncode", inputs: { text: "" } } });
    renderDialog(validation(), { onSource });

    const picker = document.querySelector<HTMLInputElement>('input[type="file"]');
    if (picker === null) throw new Error("no file input");
    await userEvent.upload(picker, new File([workflow], "wan_api.json", { type: "application/json" }));

    await waitFor(() => expect(onSource).toHaveBeenCalledWith(workflow, "wan_api.json"));
    expect(screen.getByLabelText("粘贴端点定义或 workflow")).toHaveValue(workflow);
  });

  it("sends a ComfyUI definition on to the binding editor instead of saving it here", async () => {
    const onBindNodes = vi.fn();
    const onCreateCopy = vi.fn();
    renderDialog(
      validation({ duplicates: [{ id: 7, key: "ce-7", display_name: "ComfyUI workflow", version: "1.0.0", relation: "same" }] }),
      { definition: COMFYUI_DEFINITION, onBindNodes, onCreateCopy },
    );

    // 同作者同名的判定留到编辑器里改完名字再说，所以这里不给覆盖/副本的选择。
    expect(screen.queryByRole("button", { name: "覆盖" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "去绑定节点" }));

    expect(onBindNodes).toHaveBeenCalledOnce();
    expect(onCreateCopy).not.toHaveBeenCalled();
  });
});
