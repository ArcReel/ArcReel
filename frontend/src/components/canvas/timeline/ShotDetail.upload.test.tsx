import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { makeNarrationSegment } from "@/test/factories";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";

import { ShotDetail } from "./ShotDetail";

function renderDetail(onRestoreStoryboard: () => Promise<unknown>) {
  const segment = makeNarrationSegment();
  render(
    <ShotDetail
      segment={segment}
      segmentId={segment.segment_id}
      contentMode="narration"
      aspectRatio="9:16"
      projectName="demo"
      scriptFile="episode_1.json"
      selectedIndex={0}
      totalCount={1}
      onPrev={() => {}}
      onNext={() => {}}
      durationOptions={[8]}
      onUpdatePrompt={vi.fn()}
      onRestoreStoryboard={onRestoreStoryboard}
    />,
  );
}

/** Select an image through the file input beside the storyboard card upload button. */
function chooseStoryboard() {
  const input = screen.getByRole("button", { name: "上传分镜图" }).previousElementSibling as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["png"], "shot.png", { type: "image/png" })] } });
}

describe("ShotDetail 媒体上传", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.spyOn(API, "uploadShotMedia").mockResolvedValue({
      success: true,
      path: "storyboards/E1S01.png",
      version: 2,
      asset_fingerprints: {},
    });
  });

  it("上传后项目数据已刷新时报告上传完成", async () => {
    // Notices address shots by episode position: E1S01 is second.
    useProjectsStore.setState({
      currentScripts: {
        "episode_1.json": {
          episode: 1,
          content_mode: "narration",
          segments: [makeNarrationSegment({ segment_id: "E1S00" }), makeNarrationSegment()],
        } as never,
      },
    });
    const restore = vi.fn().mockResolvedValue(true);
    renderDetail(restore);

    chooseStoryboard();

    await waitFor(() => expect(useAppStore.getState().toast).toMatchObject({ text: "「分镜 2」上传完成", tone: "success" }));
    expect(API.uploadShotMedia).toHaveBeenCalledWith("demo", "episode_1.json", "E1S01", "storyboard", expect.any(File));
  });

  it("上传后项目数据没刷新成功时（父级已提示）不再报告上传完成", async () => {
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const restore = vi.fn().mockResolvedValue(false);
    renderDetail(restore);

    chooseStoryboard();

    await waitFor(() => expect(restore).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: "上传分镜图" })).toBeEnabled());
    expect(pushToast).not.toHaveBeenCalledWith(expect.anything(), "success");
  });
});
