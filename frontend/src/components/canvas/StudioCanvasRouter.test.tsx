import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import type { EpisodeScript, ProjectData } from "@/types";

vi.mock("./OverviewCanvas", () => ({
  OverviewCanvas: () => <div data-testid="overview-canvas">Overview</div>,
}));

vi.mock("./SourceFileViewer", () => ({
  SourceFileViewer: ({ filename }: { filename: string }) => (
    <div data-testid="source-file-viewer">{filename}</div>
  ),
}));

vi.mock("./timeline/TimelineCanvas", () => ({
  TimelineCanvas: ({
    episodeScript,
    onUpdatePrompt,
    onGenerateStoryboard,
    onGenerateVideo,
  }: {
    episodeScript: unknown;
    onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
    onGenerateStoryboard?: (segmentId: string) => void;
    onGenerateVideo?: (segmentId: string) => void;
  }) => (
    <div data-testid="timeline-canvas">
      <div data-testid="timeline-has-script">{episodeScript ? "yes" : "no"}</div>
      <button onClick={() => onUpdatePrompt?.("SEG-1", "image_prompt", "new prompt")}>
        update-prompt
      </button>
      <button onClick={() => onGenerateStoryboard?.("SEG-1")}>generate-storyboard</button>
      <button onClick={() => onGenerateVideo?.("SEG-1")}>generate-video</button>
    </div>
  ),
}));

vi.mock("./lorebook/LorebookGallery", () => ({
  LorebookGallery: ({
    mode,
    onSaveCharacter,
    onUpdateClue,
    onGenerateCharacter,
    onGenerateClue,
    onAddCharacter,
    onAddClue,
  }: {
    mode: "characters" | "clues";
    onSaveCharacter: (
      name: string,
      payload: {
        description: string;
        voiceStyle: string;
        referenceFile?: File | null;
      },
    ) => Promise<void>;
    onUpdateClue: (name: string, updates: Record<string, unknown>) => void;
    onGenerateCharacter: (name: string) => void;
    onGenerateClue: (name: string) => void;
    onAddCharacter?: () => void;
    onAddClue?: () => void;
  }) => (
    <div data-testid="lorebook-gallery" data-mode={mode}>
      <button
        onClick={() =>
          void onSaveCharacter("Hero", {
            description: "new desc",
            voiceStyle: "new voice",
            referenceFile: new File(["ref"], "hero.png", { type: "image/png" }),
          })
        }
      >
        update-character
      </button>
      <button onClick={() => onGenerateCharacter("Hero")}>generate-character</button>
      <button onClick={() => onUpdateClue("Key", { description: "new clue" })}>
        update-clue
      </button>
      <button onClick={() => onGenerateClue("Key")}>generate-clue</button>
      <button onClick={() => onAddCharacter?.()}>add-character</button>
      <button onClick={() => onAddClue?.()}>add-clue</button>
    </div>
  ),
}));

vi.mock("./lorebook/AddCharacterForm", () => ({
  AddCharacterForm: ({
    onSubmit,
    onCancel,
  }: {
    onSubmit: (
      name: string,
      description: string,
      voice: string,
      referenceFile?: File | null,
    ) => Promise<void>;
    onCancel: () => void;
  }) => (
    <div data-testid="add-character-form">
      <button
        onClick={() =>
          void onSubmit(
            "NewHero",
            "desc",
            "voice",
            new File(["ref"], "new-hero.png", { type: "image/png" }),
          )
        }
      >
        submit-add-character
      </button>
      <button onClick={onCancel}>cancel-add-character</button>
    </div>
  ),
}));

vi.mock("./lorebook/AddClueForm", () => ({
  AddClueForm: ({
    onSubmit,
    onCancel,
  }: {
    onSubmit: (
      name: string,
      clueType: string,
      description: string,
      importance: string,
    ) => Promise<void>;
    onCancel: () => void;
  }) => (
    <div data-testid="add-clue-form">
      <button onClick={() => void onSubmit("NewClue", "prop", "desc", "major")}>
        submit-add-clue
      </button>
      <button onClick={onCancel}>cancel-add-clue</button>
    </div>
  ),
}));

function makeProjectData(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
    characters: {
      Hero: { description: "hero description" },
    },
    clues: {
      Key: { type: "prop", description: "key description", importance: "major" },
    },
    ...overrides,
  };
}

function makeScript(): EpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "narration",
    duration_seconds: 4,
    summary: "summary",
    novel: { title: "n", chapter: "1" },
    segments: [
      {
        segment_id: "SEG-1",
        episode: 1,
        duration_seconds: 4,
        segment_break: false,
        novel_text: "text",
        characters_in_segment: ["Hero"],
        clues_in_segment: ["Key"],
        image_prompt: "image prompt",
        video_prompt: "video prompt",
        transition_to_next: "cut",
      },
    ],
  };
}

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <StudioCanvasRouter />
    </Router>,
  );
}

describe("StudioCanvasRouter", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows loading state when currentProjectName is missing", () => {
    renderAt("/");
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("routes characters/clues/source/episodes views correctly", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: {
        "episode_1.json": makeScript(),
      },
    });

    const viewCharacters = renderAt("/characters");
    expect(screen.getByTestId("lorebook-gallery")).toHaveAttribute("data-mode", "characters");
    viewCharacters.unmount();

    const viewClues = renderAt("/clues");
    expect(screen.getByTestId("lorebook-gallery")).toHaveAttribute("data-mode", "clues");
    viewClues.unmount();

    const viewSource = renderAt("/source/source%20file.txt");
    expect(screen.getByTestId("source-file-viewer")).toHaveTextContent("source file.txt");
    viewSource.unmount();

    const viewEpisodes = renderAt("/episodes/1");
    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("timeline-has-script")).toHaveTextContent("yes");
    viewEpisodes.unmount();

    await waitFor(() => {
      expect(screen.queryByText("加载中...")).not.toBeInTheDocument();
    });
  });

  it("runs character/clue callbacks and reports API failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateCharacter").mockResolvedValue({ success: true });
    const uploadFileSpy = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValue({ success: true, path: "x", url: "y" });
    vi.spyOn(API, "generateCharacter").mockResolvedValue({ success: true, task_id: "t-1", message: "已提交" });
    const addCharacterSpy = vi.spyOn(API, "addCharacter").mockResolvedValue({ success: true });
    vi.spyOn(API, "updateClue").mockRejectedValue(new Error("clue failed"));
    vi.spyOn(API, "generateClue").mockRejectedValue(new Error("generate failed"));
    vi.spyOn(API, "addClue").mockResolvedValue({ success: true });

    renderAt("/characters");

    fireEvent.click(screen.getByText("update-character"));
    await waitFor(() => {
      expect(API.updateCharacter).toHaveBeenCalledWith("demo", "Hero", {
        description: "new desc",
        voice_style: "new voice",
      });
      expect(API.uploadFile).toHaveBeenNthCalledWith(
        1,
        "demo",
        "character_ref",
        expect.any(File),
        "Hero",
      );
      expect(API.getProject).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByText("generate-character"));
    await waitFor(() => {
      expect(API.generateCharacter).toHaveBeenCalledWith(
        "demo",
        "Hero",
        "hero description",
      );
      expect(useAppStore.getState().toast?.text).toContain("生成任务已提交");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });

    fireEvent.click(screen.getByText("add-character"));
    expect(await screen.findByTestId("add-character-form")).toBeInTheDocument();
    fireEvent.click(screen.getByText("submit-add-character"));
    await waitFor(() => {
      expect(API.addCharacter).toHaveBeenCalledWith(
        "demo",
        "NewHero",
        "desc",
        "voice",
      );
      expect(API.uploadFile).toHaveBeenNthCalledWith(
        2,
        "demo",
        "character_ref",
        expect.any(File),
        "NewHero",
      );
      expect(addCharacterSpy.mock.invocationCallOrder[0]).toBeLessThan(
        uploadFileSpy.mock.invocationCallOrder[1],
      );
    });

    fireEvent.click(screen.getByText("update-clue"));
    await waitFor(() => {
      expect(API.updateClue).toHaveBeenCalledWith("demo", "Key", {
        description: "new clue",
      });
      expect(useAppStore.getState().toast?.text).toContain("更新线索失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });

    fireEvent.click(screen.getByText("generate-clue"));
    await waitFor(() => {
      expect(API.generateClue).toHaveBeenCalledWith("demo", "Key", "key description");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });

    fireEvent.click(screen.getByText("add-clue"));
    expect(await screen.findByTestId("add-clue-form")).toBeInTheDocument();
    fireEvent.click(screen.getByText("submit-add-clue"));
    await waitFor(() => {
      expect(API.addClue).toHaveBeenCalledWith(
        "demo",
        "NewClue",
        "prop",
        "desc",
        "major",
      );
    });
  });

  it("runs timeline callbacks and handles generation failures", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateSegment").mockRejectedValue(new Error("update failed"));
    vi.spyOn(API, "generateStoryboard").mockRejectedValue(new Error("storyboard failed"));
    vi.spyOn(API, "generateVideo").mockRejectedValue(new Error("video failed"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("update-prompt"));
    await waitFor(() => {
      expect(API.updateSegment).toHaveBeenCalledWith("demo", "SEG-1", {
        image_prompt: "new prompt",
      });
      expect(useAppStore.getState().toast?.text).toContain("更新 Prompt 失败");
    });

    fireEvent.click(screen.getByText("generate-storyboard"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "image prompt",
        "episode_1.json",
      );
      expect(useAppStore.getState().toast?.text).toContain("生成分镜失败");
    });

    fireEvent.click(screen.getByText("generate-video"));
    await waitFor(() => {
      expect(API.generateVideo).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "video prompt",
        "episode_1.json",
        4,
      );
      expect(useAppStore.getState().toast?.text).toContain("生成视频失败");
    });
  });
});
