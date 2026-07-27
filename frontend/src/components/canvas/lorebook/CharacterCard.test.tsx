import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterCard } from "./CharacterCard";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
}));

function characterTask(status: TaskItem["status"]): TaskItem {
  return {
    task_id: "t1",
    project_name: "demo",
    task_type: "character",
    media_type: "image",
    resource_id: "Hero",
    resource_type: null,
    script_file: null,
    payload: {},
    status,
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("CharacterCard", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    Object.defineProperty(globalThis.URL, "createObjectURL", {
      writable: true,
      value: vi.fn(() => "blob:character-ref"),
    });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", {
      writable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("renders existing saved reference image", () => {
    render(
      <CharacterCard
        name="Hero"
        character={{
          description: "hero desc",
          voice_style: "warm",
          reference_image: "characters/refs/Hero.png",
        }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    expect(screen.getByAltText(/Hero.*参考图/)).toHaveAttribute(
      "src",
      "/api/v1/files/demo/characters/refs/Hero.png",
    );
  });

  it("keeps selected reference file until save and submits it in the payload", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={onSave}
        onGenerate={vi.fn()}
      />,
    );

    const fileInput = screen.getByLabelText("上传角色参考图");
    expect(fileInput).not.toBeNull();

    const file = new File(["ref"], "hero.png", { type: "image/png" });
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });

    expect(screen.getByText(/待保存参考图/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("Hero", {
        description: "hero desc",
        voiceStyle: "warm",
        referenceFile: file,
      });
    });
  });

  it("disables add-to-library while an image_edit/generation task is in flight", () => {
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
        generating
      />,
    );

    const addButton = screen.getByRole("button", { name: "加入资产库" });
    expect(addButton).toBeDisabled();
    expect(addButton).toHaveAttribute("title", "生成或编辑进行中，暂无法加入资产库");
  });

  it("rejects sheet upload submitted after the resource became busy post-open", async () => {
    const uploadFile = vi.spyOn(API, "uploadFile").mockResolvedValue({ path: "x" } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    const sheetInput = screen.getByLabelText("上传设计图", { selector: "input" });
    // 面板打开（点击上传按钮）之后、选完文件之前，该角色被别处入队占用。
    useTasksStore.setState({ tasks: [characterTask("running")] });

    const file = new File(["sheet"], "hero-sheet.png", { type: "image/png" });
    fireEvent.change(sheetInput as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("生成或编辑进行中，暂无法上传设计图", "info");
    });
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("auto-resizes the description textarea as content grows", async () => {
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn().mockResolvedValue(undefined)}
        onGenerate={vi.fn()}
      />,
    );

    const textarea = screen.getByPlaceholderText(/角色描述/);
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      value: 128,
    });

    fireEvent.change(textarea, { target: { value: "hero desc with more lines" } });

    await waitFor(() => {
      expect(textarea).toHaveStyle({ height: "128px" });
    });
  });

  it("renders no write entries when read-only", () => {
    render(
      <CharacterCard
        name="Hero"
        character={{
          description: "hero desc",
          voice_style: "warm",
          character_sheet: "characters/Hero.png",
        }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
        readOnly
      />,
    );

    // 内容照旧展示，只是每一个写入口都不在了
    expect(screen.getByText("Hero")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/角色描述/)).toHaveAttribute("readonly");
    expect(screen.queryByTestId("version-time-machine")).toBeNull();
    expect(screen.queryByRole("button", { name: /生成|上传|入库|保存/ })).toBeNull();
    for (const field of screen.getAllByRole("textbox")) {
      expect(field).toHaveAttribute("readonly");
    }
  });
});
