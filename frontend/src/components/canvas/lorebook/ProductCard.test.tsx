import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductCard } from "./ProductCard";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";

describe("ProductCard", () => {
  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  const product = { description: "限量款背包" };

  it("opens a prompt preview for the current description draft", async () => {
    const preview = vi.spyOn(API, "previewAssetPrompt").mockResolvedValue({
      text: "最终提示词：草稿外观", unavailable: null, is_text_form: true, warnings: [],
    });
    render(<ProductCard name="茶杯" product={{ description: "旧描述" }}
      projectName="demo" onUpdate={vi.fn()} onGenerate={vi.fn()} />);
    fireEvent.change(screen.getByDisplayValue("旧描述"), { target: { value: "草稿外观" } });
    fireEvent.click(screen.getByRole("button", { name: "查看提示词" }));
    expect(await screen.findByText("最终提示词：草稿外观")).toBeInTheDocument();
    expect(preview).toHaveBeenCalledWith("demo", "product", "茶杯", "草稿外观", { signal: expect.any(AbortSignal) });
  });

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
    expect(screen.queryByTestId("version-time-machine")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
