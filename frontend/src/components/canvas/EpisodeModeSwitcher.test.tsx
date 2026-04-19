import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EpisodeModeSwitcher } from "./EpisodeModeSwitcher";

describe("EpisodeModeSwitcher", () => {
  it("shows project-level mode when episode has none (inherited)", () => {
    render(
      <EpisodeModeSwitcher
        projectMode="reference_video"
        episodeMode={undefined}
        onChange={vi.fn()}
      />,
    );
    const radio = screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }) as HTMLInputElement;
    expect(radio.checked).toBe(true);
  });

  it("uses episode-level override when set", () => {
    render(
      <EpisodeModeSwitcher
        projectMode="storyboard"
        episodeMode="grid"
        onChange={vi.fn()}
      />,
    );
    const gridRadio = screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ }) as HTMLInputElement;
    expect(gridRadio.checked).toBe(true);
  });

  it("calls onChange with the selected mode when clicked", () => {
    const onChange = vi.fn();
    render(
      <EpisodeModeSwitcher
        projectMode="storyboard"
        episodeMode={undefined}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /Reference-to-Video|参考生视频/ }));
    expect(onChange).toHaveBeenCalledWith("reference_video");
  });
});
