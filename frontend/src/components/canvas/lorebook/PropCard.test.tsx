import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PropCard } from "./PropCard";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";

describe("PropCard", () => {
  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  const prop = { description: "古铜色钥匙" };

  it("opens a prompt preview for the current description draft", async () => {
    const preview = vi.spyOn(API, "previewAssetPrompt").mockResolvedValue({
      text: "最终提示词：草稿外观", unavailable: null, is_text_form: true, warnings: [],
    });
    render(<PropCard name="宝剑" prop={{ description: "旧描述" }}
      projectName="demo" onUpdate={vi.fn()} onGenerate={vi.fn()} />);
    fireEvent.change(screen.getByDisplayValue("旧描述"), { target: { value: "草稿外观" } });
    fireEvent.click(screen.getByRole("button", { name: "查看提示词" }));
    expect(await screen.findByText("最终提示词：草稿外观")).toBeInTheDocument();
    expect(preview).toHaveBeenCalledWith("demo", "prop", "宝剑", "草稿外观", { signal: expect.any(AbortSignal) });
  });

  it("renders name and description", () => {
    render(
      <PropCard
        name="钥匙"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.getByText("钥匙")).toBeInTheDocument();
    expect(screen.getByDisplayValue("古铜色钥匙")).toBeInTheDocument();
  });

  it("invokes onGenerate when generate button clicked", () => {
    const onGenerate = vi.fn();
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={onGenerate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /生成/ }));
    expect(onGenerate).toHaveBeenCalledWith("A");
  });

  it("shows save button only when dirty", () => {
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument();

    const textarea = screen.getByDisplayValue("古铜色钥匙");
    fireEvent.change(textarea, { target: { value: "新描述" } });
    expect(screen.getByRole("button", { name: /保存/ })).toBeInTheDocument();
  });

  it("calls onUpdate when save button clicked", () => {
    const onUpdate = vi.fn();
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={onUpdate}
        onGenerate={vi.fn()}
      />,
    );

    const textarea = screen.getByDisplayValue("古铜色钥匙");
    fireEvent.change(textarea, { target: { value: "新描述" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(onUpdate).toHaveBeenCalledWith("A", { description: "新描述" });
  });

  it("does not render importance or type badges", () => {
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.queryByText(/major|minor|主要|次要|道具类型/i)).not.toBeInTheDocument();
  });

  it("always shows generate button (not gated on importance)", () => {
    render(
      <PropCard
        name="A"
        prop={{ description: "" }}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /生成/ })).toBeInTheDocument();
  });

  it("renders no write entries when read-only", () => {
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
        readOnly
      />,
    );

    expect(screen.getByDisplayValue("古铜色钥匙")).toHaveAttribute("readonly");
    expect(screen.queryByTestId("version-time-machine")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
