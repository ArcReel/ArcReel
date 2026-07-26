import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useProjectsStore } from "@/stores/projects-store";
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

  it("disables the generate CTA when a sibling upload is in flight (uploadDisabled)", () => {
    const { getByRole } = renderCard({ onGenerate: vi.fn(), uploadDisabled: true });
    expect(getByRole("button", { name: /生成分镜/ })).toBeDisabled();
  });
});

describe("MediaCard in the demo workbench", () => {
  afterEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  // 四个入口的回调/开关全部给足：只有演示态判定本身能把它们关掉
  function renderCardWithAllEntries() {
    return renderCard({
      assetPath: "storyboards/E1S01_v1.png",
      onUpload: vi.fn(),
      onRestore: vi.fn(),
      onGenerate: vi.fn(),
      editScriptFile: "episode_1.json",
    });
  }

  it("hides the write entries and the version entry from the same judgement", () => {
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });

    const { container, queryByRole } = renderCardWithAllEntries();

    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(queryByRole("button", { name: /版本/ })).toBeNull();
    expect(queryByRole("button", { name: /编辑/ })).toBeNull();
    expect(queryByRole("button", { name: /重新生成分镜/ })).toBeNull();
  });

  it("keeps all four entries outside the demo workbench", () => {
    useProjectsStore.setState({ currentProjectName: "demo" });

    const { container, getByRole } = renderCardWithAllEntries();

    expect(container.querySelector('input[type="file"]')).not.toBeNull();
    expect(getByRole("button", { name: /版本/ })).toBeInTheDocument();
    expect(getByRole("button", { name: /编辑/ })).toBeInTheDocument();
    expect(getByRole("button", { name: /重新生成分镜/ })).toBeInTheDocument();
  });
});
