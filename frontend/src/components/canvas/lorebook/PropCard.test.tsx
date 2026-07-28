import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PropCard } from "./PropCard";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
}));


describe("PropCard", () => {
  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

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

  it("rejects sheet upload submitted after the resource became busy post-open", async () => {
    const uploadFile = vi.spyOn(API, "uploadFile").mockResolvedValue({ path: "x" } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(
      <PropCard
        name="A"
        prop={prop}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    const sheetInput = screen.getByLabelText("上传设计图", { selector: "input" });
    // 面板打开（点击上传按钮）之后、选完文件之前，该道具被别处入队占用。
    useTasksStore.setState({
      tasks: [
        makeTask({
          project_name: "demo",
          task_type: "prop",
          media_type: "image",
          resource_id: "A",
          status: "running",
        }),
      ],
    });

    const file = new File(["sheet"], "prop-sheet.png", { type: "image/png" });
    fireEvent.change(sheetInput as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("生成或编辑进行中，暂无法上传设计图", "info");
    });
    expect(uploadFile).not.toHaveBeenCalled();
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
    expect(screen.queryByTestId("version-time-machine")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
