import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MediaCard } from "./MediaCard";

function renderCard(props: Partial<Parameters<typeof MediaCard>[0]> = {}) {
  return render(
    <MediaCard
      kind="storyboard"
      projectName="demo"
      segmentId="E1S01"
      assetPath={null}
      aspectRatio="9:16"
      {...props}
    />,
  );
}

describe("MediaCard upload", () => {
  it("invokes onUpload with the selected file", () => {
    const onUpload = vi.fn();
    const { container } = renderCard({ onUpload });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const file = new File(["x"], "board.png", { type: "image/png" });
    fireEvent.change(input!, { target: { files: [file] } });
    expect(onUpload).toHaveBeenCalledWith(file);
  });

  it("hides upload entry when onUpload is not provided", () => {
    const { container } = renderCard();
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });

  it("keeps upload entry visible in grid mode where the generate CTA is hidden", () => {
    const { container } = renderCard({
      onUpload: vi.fn(),
      onGenerate: vi.fn(),
      hideGenerateButton: true,
    });
    expect(container.querySelector('input[type="file"]')).not.toBeNull();
  });

  it("disables upload button while generating", () => {
    const { container } = renderCard({ onUpload: vi.fn(), generating: true });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    const button = input?.nextElementSibling as HTMLButtonElement;
    expect(button).toBeDisabled();
  });

  it("disables upload button when a sibling upload is in flight (uploadDisabled)", () => {
    const { container } = renderCard({ onUpload: vi.fn(), uploadDisabled: true });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    const button = input?.nextElementSibling as HTMLButtonElement;
    expect(button).toBeDisabled();
  });

  it("accepts video formats for the video card", () => {
    const { container } = renderCard({ kind: "video", onUpload: vi.fn() });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input?.accept).toContain(".mp4");
  });
});
