import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n"; // ensure i18n resources loaded
import { WizardStep1Basics } from "./WizardStep1Basics";

const baseValue = {
  title: "",
  contentMode: "narration" as const,
  sourceKind: "novel" as const,
  aspectRatio: "9:16" as const,
  generationMode: "storyboard" as const,
  targetDuration: 60,
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
    // click drama option (剧集模式)
    fireEvent.click(screen.getByText(/剧集模式|Drama Mode/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ contentMode: "drama" }),
    );
  });

  it("hides the source-kind selector outside drama mode", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole("radiogroup", { name: /源文件性质|Source type|Loại tệp nguồn/ })).toBeNull();
  });

  it("emits onChange with screenplay when source kind selected in drama mode", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, contentMode: "drama" }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    const group = screen.getByRole("radiogroup", { name: /源文件性质|Source type|Loại tệp nguồn/ });
    fireEvent.click(within(group).getByText(/剧本|Screenplay|Kịch bản/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ sourceKind: "screenplay" }),
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
    // click 宫格生视频 / Grid-to-Video
    fireEvent.click(screen.getByText(/Grid-to-Video|宫格生视频/));
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

  it("marks title input as aria-required", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveAttribute("aria-required", "true");
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

  it("switches generation mode to reference_video", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, title: "t" }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ generationMode: "reference_video" }));
  });

  it("emits onChange with ad content mode", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByText(/广告\/短片|Ad \/ Short Video/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ contentMode: "ad" }),
    );
  });

  it("switching to ad resets grid generation mode to storyboard", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, generationMode: "grid" as const }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByText(/广告\/短片|Ad \/ Short Video/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ contentMode: "ad", generationMode: "storyboard" }),
    );
  });

  it("shows four target duration tiers with 60s selected by default for ad", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, contentMode: "ad" as const }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    for (const tier of ["15", "30", "60", "90"]) {
      expect(screen.getByRole("radio", { name: new RegExp(`${tier}\\s*秒`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("radio", { name: /60\s*秒/ })).toBeChecked();
  });

  it("hides target duration tiers outside ad mode", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole("radio", { name: /15\s*秒/ })).not.toBeInTheDocument();
  });

  it("emits onChange when a target duration tier is clicked", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, contentMode: "ad" as const }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /30\s*秒/ }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ targetDuration: 30 }),
    );
  });

  it("disables grid generation mode for ad", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, contentMode: "ad" as const }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ })).toBeDisabled();
  });
});
