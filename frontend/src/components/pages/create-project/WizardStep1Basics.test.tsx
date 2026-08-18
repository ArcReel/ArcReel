import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n"; // ensure i18n resources loaded
import { WizardStep1Basics } from "./WizardStep1Basics";

const baseValue = {
  title: "",
  contentMode: "narration" as const,
  sourceKind: "novel" as const,
  aspectRatio: "9:16" as const,
  generationRoute: "storyboard" as const,
  gridStoryboard: false,
  targetDuration: 60,
  speechRate: null,
};

const GRID_BAR_NAME = /多宫格分镜生视频/;

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

  it("blocks Next while the speech rate is out of range", () => {
    const onNext = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, title: "demo", speechRate: 25 }}
        onChange={() => {}}
        onNext={onNext}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /下一步/ }));
    expect(onNext).not.toHaveBeenCalled();
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
    fireEvent.click(screen.getByText(/剧情演绎|Drama/));
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

  it("keeps Next disabled until a generation route is chosen", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, title: "demo", generationRoute: null }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /下一步/ })).toBeDisabled();
  });

  it("renders the route cards with no preselection", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, generationRoute: null }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    for (const radio of screen.getAllByRole("radio", { name: /分镜图生视频|参考生视频/ })) {
      expect(radio).not.toBeChecked();
    }
  });

  it("emits onChange when the storyboard route is picked", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, generationRoute: null }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /分镜图生视频/ }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ generationRoute: "storyboard" }),
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

  it("switches to the reference route and clears the grid toggle", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, title: "t", gridStoryboard: true }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /参考生视频/ }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ generationRoute: "reference_video", gridStoryboard: false }),
    );
  });

  it("shows the grid assembly bar only on the storyboard route", () => {
    const { rerender } = render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("switch", { name: GRID_BAR_NAME })).toBeInTheDocument();

    rerender(
      <WizardStep1Basics
        value={{ ...baseValue, generationRoute: "reference_video" as const }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole("switch", { name: GRID_BAR_NAME })).not.toBeInTheDocument();
  });

  it("emits onChange when the grid toggle is switched on", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("switch", { name: GRID_BAR_NAME }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ gridStoryboard: true }));
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

  it("switching to ad clears the grid toggle", () => {
    const onChange = vi.fn();
    render(
      <WizardStep1Basics
        value={{ ...baseValue, gridStoryboard: true }}
        onChange={onChange}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByText(/广告\/短片|Ad \/ Short Video/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ contentMode: "ad", gridStoryboard: false }),
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

  it("hides the grid assembly bar for ad projects", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, contentMode: "ad" as const }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole("switch", { name: GRID_BAR_NAME })).not.toBeInTheDocument();
  });

  it("renders content mode labels with product language", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/旁白\/解说/)).toBeInTheDocument();
    expect(screen.getByText(/剧情演绎/)).toBeInTheDocument();
    expect(screen.getByText(/广告\/短片/)).toBeInTheDocument();
  });

  it("renders generation route labels with product language", () => {
    render(
      <WizardStep1Basics
        value={{ ...baseValue, generationRoute: null }}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("radio", { name: /分镜图生视频/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /参考生视频/ })).toBeInTheDocument();
  });

  it("renders the multi-grid storyboard toggle with product language", () => {
    render(
      <WizardStep1Basics
        value={baseValue}
        onChange={() => {}}
        onNext={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("switch", { name: /多宫格分镜生视频/ })).toBeInTheDocument();
  });
});
