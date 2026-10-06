import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Input } from "./input";
import { InputGroup, InputGroupInput } from "./input-group";

describe("Input 等宽字体", () => {
  it("mono 换成等宽字体，可与 variant=\"plain\" 同时使用", () => {
    render(<Input aria-label="模型 ID" mono variant="plain" />);

    const field = screen.getByRole("textbox", { name: "模型 ID" });
    expect(field).toHaveClass("font-mono");
    expect(field).toHaveClass("border-transparent");
  });

  it("默认保持比例字体", () => {
    render(<Input aria-label="接口地址" type="url" />);

    expect(screen.getByRole("textbox", { name: "接口地址" })).not.toHaveClass("font-mono");
  });

  it("InputGroupInput 透传 mono", () => {
    render(
      <InputGroup>
        <InputGroupInput aria-label="API Key" mono />
      </InputGroup>,
    );

    expect(screen.getByRole("textbox", { name: "API Key" })).toHaveClass("font-mono");
  });
});
