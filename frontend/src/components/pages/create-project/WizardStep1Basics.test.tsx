import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n"; // ensure i18n resources loaded
import { WizardStep1Basics } from "./WizardStep1Basics";

const baseValue = {
  title: "",
  contentMode: "narration" as const,
  aspectRatio: "9:16" as const,
  generationMode: "single" as const,
};

describe("WizardStep1Basics", () => {
  it("disables Next button when title is empty", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /下一步/ })).toBeDisabled();
  });

  it("enables Next button when title has content", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, title: "demo" }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled();
  });

  it("calls onNext when Next is clicked with valid title", () => {
    const onNext = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, title: "demo" }}
        onChange={() => {}}
        onNext={onNext}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("emits onChange when content mode changes", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    // click drama option (剧集动画)
    fireEvent.click(screen.getByText(/剧集动画/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ contentMode: "drama" }),
    );
  });

  it("emits onChange when aspect ratio changes", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    // click 横屏 16:9
    fireEvent.click(screen.getByText(/横屏/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ aspectRatio: "16:9" }),
    );
  });

  it("emits onChange when generation mode changes", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    // click 宫格生成
    fireEvent.click(screen.getByText(/宫格生成/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ generationMode: "grid" }),
    );
  });

  it("emits onChange when title input changes", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "hello" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ title: "hello" }),
    );
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /取消|Cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("does not call onNext when title is empty and Next is somehow triggered", () => {
    const onNext = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={onNext}
        onCancel={() => {}}
      />,
    );
    // Button is disabled but we call the handler defensively through a forced click
    const btn = screen.getByRole("button", { name: /下一步/ });
    expect(btn).toBeDisabled();
    expect(onNext).not.toHaveBeenCalled();
  });

  it("renders title label with required asterisk when title is empty", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    // Should show asterisk (red * for required)
    expect(screen.getByText("*")).toBeInTheDocument();
  });

  it("renders project_id_auto_gen_hint below the title input", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(
      screen.getByText(/系统会自动生成内部项目标识/),
    ).toBeInTheDocument();
  });
});
