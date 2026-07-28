import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GenerationModeSelector } from "./GenerationModeSelector";

function setup(overrides: Partial<React.ComponentProps<typeof GenerationModeSelector>> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <GenerationModeSelector value="storyboard" onChange={onChange} {...overrides} />,
  );
  return { ...utils, onChange };
}

describe("GenerationModeSelector", () => {
  it("renders three mode options by default", () => {
    setup();
    expect(screen.getByRole("radio", { name: /Image-to-Video|图生视频/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ })).toBeInTheDocument();
  });

  it("marks the current value as checked", () => {
    setup({ value: "reference_video" });
    const refRadio = screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }) as HTMLInputElement;
    expect(refRadio.checked).toBe(true);
  });

  it("emits onChange with canonical value when clicked", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ }));
    expect(onChange).toHaveBeenCalledWith("grid");
  });

  it("shows the description text for the selected mode", () => {
    setup({ value: "reference_video" });
    expect(
      screen.getByText(/Skip storyboards|跳过分镜/),
    ).toBeInTheDocument();
  });

  it("disables modes passed in disabledModes", () => {
    setup({ disabledModes: ["reference_video"] });
    const ref = screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }) as HTMLInputElement;
    expect(ref.disabled).toBe(true);
  });
});
