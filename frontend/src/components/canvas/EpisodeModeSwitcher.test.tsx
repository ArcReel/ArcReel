import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EpisodeModeSwitcher } from "./EpisodeModeSwitcher";
import { useAppStore } from "@/stores/app-store";

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

describe("EpisodeModeSwitcher toast on mode switch (PR7)", () => {
  it("shows 'switch to reference' toast when moving into reference_video", () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
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
    expect(pushToast).toHaveBeenCalledWith(
      expect.stringMatching(/参考生视频|reference-to-video/i),
      "info",
    );
    pushToast.mockRestore();
  });

  it("shows 'switch back' toast when leaving reference_video", () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onChange = vi.fn();
    render(
      <EpisodeModeSwitcher
        projectMode="reference_video"
        episodeMode="reference_video"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /Storyboard|图生视频/ }));
    expect(onChange).toHaveBeenCalledWith("storyboard");
    expect(pushToast).toHaveBeenCalledWith(
      expect.stringMatching(/参考模式|reference-mode|video_units/i),
      "info",
    );
    pushToast.mockRestore();
  });

  it("shows 'keep data' toast on storyboard ↔ grid swap", () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onChange = vi.fn();
    render(
      <EpisodeModeSwitcher
        projectMode="storyboard"
        episodeMode={undefined}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ }));
    expect(onChange).toHaveBeenCalledWith("grid");
    expect(pushToast).toHaveBeenCalledWith(
      expect.stringMatching(/不会删除|won't remove|switch back anytime|随时切回/i),
      "info",
    );
    pushToast.mockRestore();
  });

  it("no toast when clicking the already-active mode", () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const onChange = vi.fn();
    render(
      <EpisodeModeSwitcher
        projectMode="grid"
        episodeMode={undefined}
        onChange={onChange}
      />,
    );
    // effective = grid；再点 grid radio 应无反应
    fireEvent.click(screen.getByRole("radio", { name: /Grid-to-Video|宫格生视频/ }));
    expect(onChange).not.toHaveBeenCalled();
    expect(pushToast).not.toHaveBeenCalled();
    pushToast.mockRestore();
  });
});
