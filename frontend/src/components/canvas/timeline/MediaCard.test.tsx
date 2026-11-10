import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useProjectsStore } from "@/stores/projects-store";
import { API } from "@/api";
import { createDeferred } from "@/test/deferred";
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

describe("MediaCard storyboard image", () => {
  afterEach(() => useProjectsStore.setState({ assetFingerprints: {} }));

  it("offers 640 and 1280 thumbnails to the card and keeps the original for the full-screen preview", () => {
    useProjectsStore.setState({ assetFingerprints: { "storyboards/E1S01.png": 5 } });
    renderCard({ assetPath: "storyboards/E1S01.png" });

    const card = screen.getByRole("img", { name: /分镜图$/ });
    expect(card).toHaveAttribute(
      "srcset",
      "/api/v1/files/demo/storyboards/E1S01.png?v=5&w=640 640w, /api/v1/files/demo/storyboards/E1S01.png?v=5&w=1280 1280w",
    );
    expect(card).toHaveAttribute("src", "/api/v1/files/demo/storyboards/E1S01.png?v=5");

    fireEvent.click(screen.getByRole("button", { name: /分镜图」的大图$/ }));
    const preview = screen.getAllByRole("img", { name: /分镜图$/ }).find((img) => !img.hasAttribute("srcset"));
    expect(preview).toHaveAttribute("src", "/api/v1/files/demo/storyboards/E1S01.png?v=5");
  });
});

describe("MediaCard upload", () => {
  it.each(["success", "failure"])("blocks sibling writes until version deletion settles (%s)", async (outcome) => {
    const pending = createDeferred<Awaited<ReturnType<typeof API.deleteFileViewEntry>>>();
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "storyboards",
      resource_id: "E1S01",
      current_version: 2,
      versions: [1, 2].map((version) => ({
        version, filename: `v${version}.png`, file: `versions/storyboards/E1S01_v${version}.png`,
        created_at: "2026-02-01", file_size: 1, is_current: version === 2,
      })),
    });
    vi.spyOn(API, "deleteFileViewEntry").mockReturnValue(pending.promise);
    renderCard({ onUpload: vi.fn(), onGenerate: vi.fn(), onRestore: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: /版本/ }));
    fireEvent.click(await screen.findByRole("button", { name: "将 v1 移入项目回收站" }));
    fireEvent.click(screen.getByRole("button", { name: "v1：移入项目回收站？" }));

    expect(screen.getByRole("button", { name: "上传分镜图" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /生成分镜/ })).toBeDisabled();
    await act(async () => {
      if (outcome === "success") pending.resolve({ record: { id: "rec-1" } } as Awaited<ReturnType<typeof API.deleteFileViewEntry>>);
      else pending.reject(new Error("delete failed"));
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "上传分镜图" })).toBeEnabled());
    expect(screen.getByRole("button", { name: /生成分镜/ })).toBeEnabled();
  });

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

  it("disables upload button while generating", () => {
    renderCard({ onUpload: vi.fn(), generating: true });
    expect(screen.getByRole("button", { name: "上传分镜图" })).toBeDisabled();
  });

  it("disables upload button when a sibling upload is in flight (uploadDisabled)", () => {
    renderCard({ onUpload: vi.fn(), uploadDisabled: true });
    expect(screen.getByRole("button", { name: "上传分镜图" })).toBeDisabled();
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
    expect(queryByRole("button", { name: /版本/ })).not.toBeInTheDocument();
    expect(queryByRole("button", { name: /编辑/ })).not.toBeInTheDocument();
    expect(queryByRole("button", { name: /重新生成分镜/ })).not.toBeInTheDocument();
  });

  it("keeps all four entries outside the demo workbench", () => {
    useProjectsStore.setState({ currentProjectName: "demo" });

    const { container, getByRole } = renderCardWithAllEntries();

    expect(container.querySelector('input[type="file"]')).not.toBeNull();
    expect(getByRole("button", { name: /版本/ })).toBeInTheDocument();
    expect(getByRole("button", { name: /局部修改/ })).toBeInTheDocument();
    expect(getByRole("button", { name: /重新生成分镜/ })).toBeInTheDocument();
  });
});
