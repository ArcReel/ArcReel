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
  it("tells the user which ArcReel version the definition needs when the app is older", () => {
    renderDialog(validation({ min_app_version: { required: "0.31.0", current: "0.30.0", satisfied: false } }));

    expect(screen.getByText("该定义需要 ArcReel ≥ 0.31.0，当前为 0.30.0，导入后可能无法正常使用。")).toBeInTheDocument();
  });

  it("stays quiet when the app meets the requirement", () => {
    renderDialog(validation({ min_app_version: { required: "0.30.0", current: "0.30.0", satisfied: true } }));

    expect(screen.queryByText(/该定义需要 ArcReel/)).not.toBeInTheDocument();
  });
});
