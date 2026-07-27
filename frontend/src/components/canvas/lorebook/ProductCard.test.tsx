import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductCard } from "./ProductCard";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
}));


describe("ProductCard", () => {
  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  const product = { description: "限量款背包" };

  it("renders name and description", () => {
    render(
      <ProductCard
        name="A"
        product={product}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.getByDisplayValue("限量款背包")).toBeInTheDocument();
  });

  it("rejects sheet upload submitted after the resource became busy post-open", async () => {
    const uploadFile = vi.spyOn(API, "uploadFile").mockResolvedValue({ path: "x" } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(
      <ProductCard
        name="A"
        product={product}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    const sheetInput = screen.getByLabelText("上传设计图", { selector: "input" });
    // 面板打开（点击上传按钮）之后、选完文件之前，该商品被别处入队占用。
    useTasksStore.setState({
      tasks: [
        makeTask({
          project_name: "demo",
          task_type: "product",
          media_type: "image",
          resource_id: "A",
          status: "running",
        }),
      ],
    });

    const file = new File(["sheet"], "product-sheet.png", { type: "image/png" });
    fireEvent.change(sheetInput as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("生成或编辑进行中，暂无法上传设计图", "info");
    });
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("renders no write entries when read-only", () => {
    render(
      <ProductCard
        name="A"
        product={product}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
        readOnly
      />,
    );

    expect(screen.getByDisplayValue("限量款背包")).toHaveAttribute("readonly");
    expect(screen.queryByTestId("version-time-machine")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
