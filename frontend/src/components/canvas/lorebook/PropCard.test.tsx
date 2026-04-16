import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PropCard } from "./PropCard";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
}));

describe("PropCard", () => {
  const prop = { description: "古铜色钥匙" };

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

  it("renders VersionTimeMachine", () => {
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.getByTestId("version-time-machine")).toBeInTheDocument();
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
    expect(screen.queryByText(/major|minor|主要|次要|道具类型/i)).toBeNull();
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
});
