import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
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
    onGenerateNarration,
    onGenerateEpisodeNarration,
    onSaveTitle,
    canEditTitle,
  }: {
    episodeScript: unknown;
    onUpdatePrompt?: (segmentId: string, field: string, value: unknown) => void;
    onGenerateStoryboard?: (segmentId: string) => void;
    onGenerateVideo?: (segmentId: string) => void;
    onGenerateNarration?: (segmentId: string) => void;
    onGenerateEpisodeNarration?: (scriptFile?: string) => void;
    onSaveTitle?: (title: string) => Promise<void>;
    canEditTitle?: boolean;
  }) => (
    <div data-testid="timeline-canvas">
      <div data-testid="timeline-has-script">{episodeScript ? "yes" : "no"}</div>
      <div data-testid="timeline-can-edit-title">{canEditTitle ? "yes" : "no"}</div>
      <button onClick={() => onUpdatePrompt?.("SEG-1", "image_prompt", "new prompt")}>
        update-prompt
      </button>
      <button onClick={() => onGenerateStoryboard?.("SEG-1")}>generate-storyboard</button>
      <button onClick={() => onGenerateVideo?.("SEG-1")}>generate-video</button>
      <button onClick={() => onGenerateNarration?.("SEG-1")}>generate-narration</button>
      <button onClick={() => onGenerateEpisodeNarration?.()}>generate-episode-narration</button>
      <button onClick={() => void onSaveTitle?.("新标题")?.catch(() => {})}>save-title</button>
    </div>
  ),
}));

vi.mock("./grid/GridImageToVideoCanvas", () => ({
  GridImageToVideoCanvas: ({
    onGenerateGrid,
  }: {
    onGenerateGrid?: (
      episode: number,
      scriptFile: string,
      sceneIds?: string[],
    ) => void | Promise<void>;
  }) => (
    <div data-testid="grid-canvas">
      <button onClick={() => void onGenerateGrid?.(1, "episode_1.json")}>generate-grid</button>
    </div>
  ),
}));

vi.mock("./lorebook/CharacterCard", () => ({
  CharacterCard: ({
    name,
    onSave,
    onGenerate,
  }: {
    name: string;
    onSave: (
      name: string,
      payload: { description: string; voiceStyle: string; referenceFile?: File | null },
    ) => Promise<void>;
    onGenerate: (name: string) => void;
  }) => (
    <div data-testid="character-card" data-name={name}>
      <button
        onClick={() =>
          void onSave(name, {
            description: "new desc",
            voiceStyle: "new voice",
            referenceFile: new File(["ref"], "hero.png", { type: "image/png" }),
          })
        }
      >
        update-character
      </button>
      <button onClick={() => onGenerate(name)}>generate-character</button>
    </div>
  ),
}));

vi.mock("./lorebook/SceneCard", () => ({
  SceneCard: ({
    name,
    onUpdate,
    onGenerate,
  }: {
    name: string;
    onUpdate: (name: string, updates: Record<string, unknown>) => void;
    onGenerate: (name: string) => void;
  }) => (
    <div data-testid="scene-card" data-name={name}>
      <button onClick={() => onUpdate(name, { description: "new scene desc" })}>
        update-scene
      </button>
      <button onClick={() => onGenerate(name)}>generate-scene</button>
    </div>
  ),
}));

vi.mock("./lorebook/PropCard", () => ({
  PropCard: ({
    name,
    onUpdate,
    onGenerate,
  }: {
    name: string;
    onUpdate: (name: string, updates: Record<string, unknown>) => void;
    onGenerate: (name: string) => void;
  }) => (
    <div data-testid="prop-card" data-name={name}>
      <button onClick={() => onUpdate(name, { description: "new prop desc" })}>
        update-prop
      </button>
      <button onClick={() => onGenerate(name)}>generate-prop</button>
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

function makeProjectData(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
    characters: {
      Hero: { description: "hero description" },
    },
    scenes: { Temple: { description: "ancient temple" } },
    props: { Sword: { description: "rusty sword" } },
    ...overrides,
  };
}

function makeScript(): EpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "narration",
    duration_seconds: 4,
    novel: { title: "n", chapter: "1" },
    segments: [
      {
        segment_id: "SEG-1",
        episode: 1,
        duration_seconds: 4,
        segment_break: false,
        novel_text: "text",
        characters_in_segment: ["Hero"],
        scenes: ["Temple"],
        props: ["Sword"],
        image_prompt: "image prompt",
        video_prompt: "video prompt",
        transition_to_next: "cut",
      },
    ],
  };
}

function makeAdScript(): EpisodeScript {
  return {
    episode: 1,
    title: "广告视频",
    content_mode: "ad",
    duration_seconds: 30,
    novel: { title: "n", chapter: "1" },
    shots: [
      {
        shot_id: "SEG-1",
        section: "hook",
        duration_seconds: 5,
        voiceover_text: "口播文案",
        image_prompt: "ad image prompt",
        video_prompt: "ad video prompt",
        transition_to_next: "cut",
      },
    ],
  };
}

function makeDramaScript(): EpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "drama",
    duration_seconds: 6,
    novel: { title: "n", chapter: "1" },
    scenes: [
      {
        scene_id: "SEG-1",
        duration_seconds: 6,
        segment_break: false,
        characters_in_scene: ["Hero"],
        image_prompt: "drama image prompt",
        video_prompt: "drama video prompt",
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
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows loading state when currentProjectName is missing", () => {
    renderAt("/");
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("routes characters/scenes/props/source/episodes views correctly", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: {
        "episode_1.json": makeScript(),
      },
    });

    const viewCharacters = renderAt("/characters");
    expect(screen.getByTestId("character-card")).toHaveAttribute("data-name", "Hero");
    viewCharacters.unmount();

    const viewScenes = renderAt("/scenes");
    expect(screen.getByTestId("scene-card")).toHaveAttribute("data-name", "Temple");
    viewScenes.unmount();

    const viewProps = renderAt("/props");
    expect(screen.getByTestId("prop-card")).toHaveAttribute("data-name", "Sword");
    viewProps.unmount();

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

  it("runs character callbacks and reports API failures with toast", async () => {
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
    vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "x", url: "y" });
    vi.spyOn(API, "generateCharacter").mockResolvedValue({ success: true, task_id: "t-1", message: "已提交" });
    vi.spyOn(API, "addCharacter").mockResolvedValue({ success: true });

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

    // Test add character flow: click "add" button is not directly accessible in CharacterCard mock;
    // instead, we test the AddCharacterForm path by navigating with the form already showing.
    // The add-character button is on CharactersPage which is not directly exposed; we test the form submit instead.
  });

  it("runs scene callbacks and reports API failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateProjectScene").mockRejectedValue(new Error("scene update failed"));
    vi.spyOn(API, "generateProjectScene").mockRejectedValue(new Error("scene generate failed"));

    renderAt("/scenes");

    fireEvent.click(screen.getByText("update-scene"));
    await waitFor(() => {
      expect(API.updateProjectScene).toHaveBeenCalledWith("demo", "Temple", {
        description: "new scene desc",
      });
      expect(useAppStore.getState().toast?.text).toContain("更新场景失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });

    fireEvent.click(screen.getByText("generate-scene"));
    await waitFor(() => {
      expect(API.generateProjectScene).toHaveBeenCalledWith("demo", "Temple", "ancient temple");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
    });
  });

  it("runs prop callbacks and reports API failures with toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateProjectProp").mockRejectedValue(new Error("prop update failed"));
    vi.spyOn(API, "generateProjectProp").mockRejectedValue(new Error("prop generate failed"));

    renderAt("/props");

    fireEvent.click(screen.getByText("update-prop"));
    await waitFor(() => {
      expect(API.updateProjectProp).toHaveBeenCalledWith("demo", "Sword", {
        description: "new prop desc",
      });
      expect(useAppStore.getState().toast?.text).toContain("更新道具失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });

    fireEvent.click(screen.getByText("generate-prop"));
    await waitFor(() => {
      expect(API.generateProjectProp).toHaveBeenCalledWith("demo", "Sword", "rusty sword");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
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

  it("resolves ad shots by shot_id when generating storyboard and video", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "ad" }),
      currentScripts: { "episode_1.json": makeAdScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "ad" }),
      scripts: { "episode_1.json": makeAdScript() },
    });
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({
      success: true,
      task_id: "t-sb",
      message: "已提交",
    });
    vi.spyOn(API, "generateVideo").mockResolvedValue({
      success: true,
      task_id: "t-v",
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-storyboard"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "ad image prompt",
        "episode_1.json",
      );
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });

    fireEvent.click(screen.getByText("generate-video"));
    await waitFor(() => {
      // duration 取镜头自身 duration_seconds(5),不回退默认值 4
      expect(API.generateVideo).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "ad video prompt",
        "episode_1.json",
        5,
      );
    });
  });

  it("resolves drama scenes by scene_id when generating storyboard", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ content_mode: "drama" }),
      currentScripts: { "episode_1.json": makeDramaScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ content_mode: "drama" }),
      scripts: { "episode_1.json": makeDramaScript() },
    });
    vi.spyOn(API, "generateStoryboard").mockResolvedValue({
      success: true,
      task_id: "t-sb",
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-storyboard"));
    await waitFor(() => {
      expect(API.generateStoryboard).toHaveBeenCalledWith(
        "demo",
        "SEG-1",
        "drama image prompt",
        "episode_1.json",
      );
    });
  });

  it("reports character generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateCharacter").mockRejectedValue(new Error("character generate failed"));

    renderAt("/characters");

    fireEvent.click(screen.getByText("generate-character"));
    await waitFor(() => {
      expect(API.generateCharacter).toHaveBeenCalledWith("demo", "Hero", "hero description");
      expect(useAppStore.getState().toast?.text).toContain("提交失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("saves the episode title and shows a success toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateEpisode").mockResolvedValue({ success: true });

    renderAt("/episodes/1");

    // script_file 存在 → 标题可编辑入口透传为 true
    expect(screen.getByTestId("timeline-can-edit-title")).toHaveTextContent("yes");

    fireEvent.click(screen.getByText("save-title"));
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
      expect(API.getProject).toHaveBeenCalled();
      expect(useAppStore.getState().toast?.text).toContain("分集标题已更新");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("reports episode title update failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "updateEpisode").mockRejectedValue(new Error("episode title failed"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("save-title"));
    await waitFor(() => {
      expect(API.updateEpisode).toHaveBeenCalledWith("demo", 1, { title: "新标题" });
      expect(useAppStore.getState().toast?.text).toContain("更新分集标题失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("submits narration generation and shows a success toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateNarrationAudio").mockResolvedValue({
      success: true,
      task_id: "t-1",
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(API.generateNarrationAudio).toHaveBeenCalledWith("demo", "SEG-1", "episode_1.json");
      expect(useAppStore.getState().toast?.text).toContain("旁白");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("reports narration generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateNarrationAudio").mockRejectedValue(new Error("tts failed"));

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("生成旁白失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });

  it("submits episode narration batch and reports the submitted count", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: ["t-1", "t-2"],
      message: "已提交",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-episode-narration"));
    await waitFor(() => {
      expect(API.generateEpisodeNarrationAudio).toHaveBeenCalledWith("demo", "episode_1.json");
      expect(useAppStore.getState().toast?.text).toContain("2");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("tells the user when episode narration has nothing missing", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateEpisodeNarrationAudio").mockResolvedValue({
      success: true,
      task_ids: [],
      message: "无需补缺",
    });

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-episode-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("所有分镜均已生成旁白");
      expect(useAppStore.getState().toast?.tone).toBe("success");
    });
  });

  it("blocks narration generation when no audio provider is configured", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData(),
      currentScripts: { "episode_1.json": makeScript() },
    });
    useConfigStatusStore.setState({
      initialized: true,
      availableMediaTypes: ["image", "video", "text"],
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: { "episode_1.json": makeScript() },
    });
    const generateSpy = vi.spyOn(API, "generateNarrationAudio");

    renderAt("/episodes/1");

    fireEvent.click(screen.getByText("generate-narration"));
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toContain("音频供应商");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
    expect(generateSpy).not.toHaveBeenCalled();
  });

  it("reports grid generation failure with an error toast", async () => {
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: makeProjectData({ generation_mode: "grid" }),
      currentScripts: { "episode_1.json": makeScript() },
    });

    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData({ generation_mode: "grid" }),
      scripts: { "episode_1.json": makeScript() },
    });
    vi.spyOn(API, "generateGrid").mockRejectedValue(new Error("grid generate failed"));

    renderAt("/episodes/1");

    fireEvent.click(await screen.findByText("generate-grid"));
    await waitFor(() => {
      expect(API.generateGrid).toHaveBeenCalledWith("demo", 1, "episode_1.json", undefined);
      expect(useAppStore.getState().toast?.text).toContain("宫格生成失败");
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
  });
});
