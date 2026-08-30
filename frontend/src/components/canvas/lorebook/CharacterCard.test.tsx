import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterCard } from "./CharacterCard";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", async () => {
  const { versionTimeMachineMock } = await import("@/__mocks__/VersionTimeMachine");
  return versionTimeMachineMock();
});


describe("CharacterCard", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
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

  it("keeps the voice style field addressable under the grouped 声音 heading", () => {
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    // 「声音」是描述 + 音频样本共用的分组标题，不能吃掉描述输入自身的字段身份
    expect(screen.getByLabelText("声音风格")).toHaveValue("warm");
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
    expect(fileInput).toBeInTheDocument();

    const file = new File(["ref"], "hero.png", { type: "image/png" });
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });

    expect(screen.getByText(/待保存参考图/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("Hero", {
        description: "hero desc",
        voiceStyle: "warm",
        referenceFile: file,
        audioFile: null,
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

    const sheetInput = screen.getByLabelText("上传资产图", { selector: "input" });
    // 面板打开（点击上传按钮）之后、选完文件之前，该角色被别处入队占用。
    useTasksStore.setState({
      tasks: [
        makeTask({
          project_name: "demo",
          task_type: "character",
          media_type: "image",
          resource_id: "Hero",
          status: "running",
        }),
      ],
    });

    const file = new File(["sheet"], "hero-sheet.png", { type: "image/png" });
    fireEvent.change(sheetInput as HTMLInputElement, { target: { files: [file] } });

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("生成或编辑进行中，暂无法上传资产图", "info");
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

  it("shows the upload placeholder and format hint when no audio sample exists", () => {
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    expect(screen.getByText("上传参考音频")).toBeInTheDocument();
    expect(screen.getByText("wav / mp3 · 2–10 秒 · ≤15MB")).toBeInTheDocument();
  });

  it("keeps selected audio file until save and submits it in the payload", async () => {
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

    const audioInput = screen.getByLabelText("上传角色参考音频");
    const file = new File(["audio"], "hero.wav", { type: "audio/wav" });
    fireEvent.change(audioInput as HTMLInputElement, { target: { files: [file] } });

    expect(screen.getByText(/待保存音频/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("Hero", {
        description: "hero desc",
        voiceStyle: "warm",
        referenceFile: null,
        audioFile: file,
      });
    });
  });

  it("cancels a pending audio upload without calling the delete API", () => {
    const deleteSpy = vi.spyOn(API, "deleteCharacterReferenceAudio");
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    const audioInput = screen.getByLabelText("上传角色参考音频");
    const file = new File(["audio"], "hero.wav", { type: "audio/wav" });
    fireEvent.change(audioInput as HTMLInputElement, { target: { files: [file] } });
    expect(screen.getByText(/待保存音频/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消待上传" }));

    expect(screen.getByText("上传参考音频")).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it("deletes saved reference audio immediately and reloads", async () => {
    const deleteSpy = vi.spyOn(API, "deleteCharacterReferenceAudio").mockResolvedValue({ success: true } as never);
    const onReload = vi.fn().mockResolvedValue(undefined);
    render(
      <CharacterCard
        name="Hero"
        character={{
          description: "hero desc",
          voice_style: "warm",
          reference_audio: "characters/refs_audio/Hero.wav",
        }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
        onReload={onReload}
      />,
    );

    expect(screen.getByText(/已保存音频/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除音频样本" }));

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith("demo", "Hero");
    });
    expect(onReload).toHaveBeenCalled();
  });

  it("rejects audio delete submitted after the resource became busy post-open", async () => {
    const deleteSpy = vi.spyOn(API, "deleteCharacterReferenceAudio").mockResolvedValue({ success: true } as never);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(
      <CharacterCard
        name="Hero"
        character={{
          description: "hero desc",
          voice_style: "warm",
          reference_audio: "characters/refs_audio/Hero.wav",
        }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    useTasksStore.setState({
      tasks: [
        makeTask({
          project_name: "demo",
          task_type: "character",
          media_type: "image",
          resource_id: "Hero",
          status: "running",
        }),
      ],
    });

    fireEvent.click(screen.getByRole("button", { name: "删除音频样本" }));

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("生成或编辑进行中，暂无法删除音频样本", "info");
    });
    expect(deleteSpy).not.toHaveBeenCalled();
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
    expect(screen.queryByTestId("version-time-machine")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成|上传|入库|保存/ })).not.toBeInTheDocument();
    for (const field of screen.getAllByRole("textbox")) {
      expect(field).toHaveAttribute("readonly");
    }
  });

  describe("角色声音绑定方式", () => {
    const character = { description: "hero desc", voice_style: "warm" };

    it("默认档（提示词软约束）把参考音频区折叠为可选，并说明怎样才生效", () => {
      render(
        <CharacterCard
          name="Hero"
          character={character}
          projectName="demo"
          onSave={vi.fn()}
          onGenerate={vi.fn()}
        />,
      );

      // 声音描述仍是主输入；参考音频区退到折叠壳内
      expect(screen.getByLabelText("声音风格")).toHaveValue("warm");
      expect(screen.getByText(/可选：参考音频/)).toBeInTheDocument();
      expect(screen.getByText(/按声音描述配音，参考音频暂不生效/)).toBeInTheDocument();
    });

    it("参考音频档下参考音频区照常直接展示", () => {
      render(
        <CharacterCard
          name="Hero"
          character={character}
          projectName="demo"
          voiceBinding="reference_audio"
          onSave={vi.fn()}
          onGenerate={vi.fn()}
        />,
      );

      expect(screen.queryByText(/可选：参考音频/)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /上传参考音频/ })).toBeInTheDocument();
    });
  });
});
