import { fireEvent, render, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InlineWarning } from "./InlineWarning";

describe("InlineWarning", () => {
  it("整块以 alert 暴露，正文可读", () => {
    const { getByRole } = render(<InlineWarning message="当前模型不支持尾帧" />);
    expect(getByRole("alert")).toHaveTextContent("当前模型不支持尾帧");
  });

  it("不给 action 时不渲染任何按钮", () => {
    const { getByRole } = render(<InlineWarning message="仅提示" />);
    expect(within(getByRole("alert")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("action 以可访问名暴露并可点击", () => {
    const onClick = vi.fn();
    const { getByRole } = render(
      <InlineWarning message="时长超出范围" action={{ label: "恢复自动", onClick }} />,
    );
    const button = within(getByRole("alert")).getByRole("button", { name: "恢复自动" });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("action 禁用时不可点击并带 hover 原因", () => {
    const onClick = vi.fn();
    const { getByRole } = render(
      <InlineWarning
        message="时长超出范围"
        action={{ label: "恢复自动", onClick, disabled: true, title: "生成中，暂不可改" }}
      />,
    );
    const button = within(getByRole("alert")).getByRole("button", { name: "恢复自动" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "生成中，暂不可改");
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("外层间距可由使用处指定，排版类不被覆盖", () => {
    const { getByRole } = render(<InlineWarning message="提示" className="px-3 pb-2" />);
    const alert = getByRole("alert");
    expect(alert).toHaveClass("px-3", "pb-2", "text-amber-300", "text-[12px]");
  });
});
