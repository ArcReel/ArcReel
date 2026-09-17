import userEvent from "@testing-library/user-event";
import { newEndpointDefinition } from "./endpoint-definition-draft";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EndpointValidateResponse } from "@/types";
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

function renderDialog(result: EndpointValidateResponse) {
  render(
    <EndpointImportDialog
      open
      fileName="demo.json"
      definition={null}
      validation={result}
      busy={false}
      onCreateCopy={vi.fn()}
      onOverwrite={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
}

describe("EndpointImportDialog", () => {
  it("preserves immediate overwrite, copy and cancel actions after sharing the choices", async () => {
    const onOverwrite = vi.fn();
    const onCreateCopy = vi.fn();
    const onCancel = vi.fn();
    render(<EndpointImportDialog open fileName="demo.json" definition={newEndpointDefinition("Demo")} validation={validation({ duplicates: [{ id: 7, key: "ce-7", display_name: "Demo", version: "1.0.0", relation: "same" }] })} busy={false} onOverwrite={onOverwrite} onCreateCopy={onCreateCopy} onCancel={onCancel} />);
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

    expect(screen.getByText(/Export \(API\)/)).toBeInTheDocument();
    expect(screen.getByText("文件中的错误修正后才能导入。")).toBeInTheDocument();
  });

  it("keeps the shape notice out of the way for an ordinary endpoint definition", () => {
    renderDialog(validation());

    expect(screen.queryByText(/ComfyUI/)).not.toBeInTheDocument();
  });
});
