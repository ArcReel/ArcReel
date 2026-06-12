import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NarrationAudioCard } from "./NarrationAudioCard";

function renderCard(props: Partial<Parameters<typeof NarrationAudioCard>[0]> = {}) {
  return render(
    <NarrationAudioCard
      projectName="demo"
      segmentId="E1S01"
      novelText="夜色深沉，山道蜿蜒。"
      assetPath={null}
      {...props}
    />,
  );
}

describe("NarrationAudioCard", () => {
  it("shows the readonly novel text", () => {
    renderCard();
    expect(screen.getByText("夜色深沉，山道蜿蜒。")).toBeInTheDocument();
  });

  it("renders an accessible audio player when narration audio exists", () => {
    const { container } = renderCard({ assetPath: "audio/segment_E1S01.wav" });
    const audio = container.querySelector("audio");
    expect(audio).not.toBeNull();
    expect(audio).toHaveAttribute("controls");
    expect(audio?.getAttribute("src")).toContain("audio/segment_E1S01.wav");
    expect(audio?.getAttribute("aria-label")).toBeTruthy();
  });

  it("falls back to the placeholder when novel text is whitespace-only", () => {
    renderCard({ novelText: "   \n " });
    expect(screen.getByText("（暂无原文）")).toBeInTheDocument();
  });

  it("shows a placeholder instead of a player before generation", () => {
    const { container } = renderCard();
    expect(container.querySelector("audio")).toBeNull();
    expect(screen.getByText("尚未生成")).toBeInTheDocument();
  });

  it("invokes onGenerate when the generate button is clicked", () => {
    const onGenerate = vi.fn();
    renderCard({ onGenerate });
    fireEvent.click(screen.getByRole("button", { name: /生成旁白/ }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it("labels the button as regenerate when audio already exists", () => {
    renderCard({ assetPath: "audio/segment_E1S01.wav", onGenerate: vi.fn() });
    expect(screen.getByRole("button", { name: /重新生成旁白/ })).toBeInTheDocument();
  });

  it("disables the generate button while generating", () => {
    renderCard({ onGenerate: vi.fn(), generating: true });
    expect(screen.getByRole("button", { name: /生成旁白/ })).toBeDisabled();
  });

  it("shows the estimated cost on the generate button", () => {
    renderCard({ onGenerate: vi.fn(), estimatedCost: { CNY: 0.008 } });
    expect(screen.getByRole("button", { name: /生成旁白/ }).textContent).toContain("¥");
  });
});
