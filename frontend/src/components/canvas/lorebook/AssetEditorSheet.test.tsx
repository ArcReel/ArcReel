import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API, type AssetRenameResult } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { AssetSheetStatusRow, AssetSheetType, ProjectData } from "@/types";
import { AssetGallery } from "./AssetGallery";
import type { GalleryAssetSource } from "./gallery-model";

const BUCKET = { character: "characters", scene: "scenes", prop: "props", product: "products" } as const;

function makeProject(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "drama",
    style: "Anime",
    episodes: [],
    characters: {
      林夕: { description: "白衣少女", voice_style: "清冷", character_sheet: "characters/林夕.png" },
      苏白: { description: "黑衣剑客" },
    },
    scenes: { 庭院: { description: "" } },
    props: { 玉佩: { description: "温润白玉" } },
    products: { 保温杯: { description: "不锈钢", brand: "暖暖", selling_points: ["保温 12 小时"] } },
    ...overrides,
  };
}

function row(type: AssetSheetType, name: string): AssetSheetStatusRow {
  return {
    unit_id: `${type}/${name}`,
    asset_type: type,
    name,
    derivative: null,
    status: "current",
    description_missing: false,
    image_to_image: false,
  };
}

let server: ProjectData;

function Harness({
  assetType,
  readOnly,
  onGenerate,
}: {
  assetType: AssetSheetType;
  readOnly: boolean;
  onGenerate: (name: string) => unknown;
}) {
  const project = useProjectsStore((s) => s.currentProjectData);
  const assets = (project?.[BUCKET[assetType]] ?? {}) as Record<string, GalleryAssetSource>;
  return (
    <AssetGallery
      projectName="demo"
      assetType={assetType}
      title="资产"
      assets={assets}
      readOnly={readOnly}
      onGenerate={onGenerate as (name: string) => void}
    />
  );
}

function renderGallery(assetType: AssetSheetType = "character", { readOnly = false } = {}) {
  const onGenerate = vi.fn();
  const location = memoryLocation({ path: "/", record: true });
  const wrap = (children: ReactNode) => (
    <Router hook={location.hook}>
      <LeaveGuardProvider>{children}</LeaveGuardProvider>
    </Router>
  );
  render(wrap(<Harness assetType={assetType} readOnly={readOnly} onGenerate={onGenerate} />));
  return { onGenerate };
}

async function openAsset(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name }));
  return screen.findByRole("dialog", { name });
}

/** 服务端改写一份数据：后续刷新拿到的就是它。 */
function patchServer(type: AssetSheetType, name: string, patch: Record<string, unknown>) {
  const bucket = server[BUCKET[type]] as unknown as Record<string, Record<string, unknown>>;
  bucket[name] = { ...bucket[name], ...patch };
  return Promise.resolve({ success: true });
}

describe("资产详情 Sheet", () => {
  beforeEach(() => {
    server = makeProject();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: structuredClone(server) });
    vi.spyOn(API, "getProject").mockImplementation(() =>
      Promise.resolve({ project: structuredClone(server), scripts: {} }),
    );
    vi.spyOn(API, "getAssetSheetStatus").mockResolvedValue({
      assets: [row("character", "林夕"), row("character", "苏白")],
    });
    vi.spyOn(API, "updateCharacter").mockImplementation((_p, name, patch) => patchServer("character", name, patch));
    vi.spyOn(API, "updateProjectScene").mockImplementation((_p, name, patch) => patchServer("scene", name, patch));
    vi.spyOn(API, "updateProjectProp").mockImplementation((_p, name, patch) => patchServer("prop", name, patch));
    vi.spyOn(API, "updateProjectProduct").mockImplementation((_p, name, patch) => patchServer("product", name, patch));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  it("asks before switching to the next asset with unsaved changes, then saves and switches", async () => {
    const user = userEvent.setup();
    renderGallery();
    const sheet = await openAsset(user, "林夕");

    const description = within(sheet).getByRole("textbox", { name: "描述" });
    await user.clear(description);
    await user.type(description, "红衣少女");
    await user.click(within(sheet).getByRole("button", { name: "下一个" }));

    const leave = await screen.findByRole("alertdialog", { name: "「林夕」有未保存的修改" });
    expect(API.updateCharacter).not.toHaveBeenCalled();
    await user.click(within(leave).getByRole("button", { name: "保存并切换" }));

    expect(await screen.findByRole("dialog", { name: "苏白" })).toBeInTheDocument();
    expect(API.updateCharacter).toHaveBeenCalledWith("demo", "林夕", { description: "红衣少女" });
  });

  it("asks before closing with unsaved changes and keeps nothing after discarding", async () => {
    const user = userEvent.setup();
    renderGallery();
    const sheet = await openAsset(user, "林夕");

    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，佩剑");
    await user.click(within(sheet).getByRole("button", { name: "关闭" }));
    const leave = await screen.findByRole("alertdialog", { name: "「林夕」有未保存的修改" });
    await user.click(within(leave).getByRole("button", { name: "放弃修改" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "林夕" })).not.toBeInTheDocument());
    expect(API.updateCharacter).not.toHaveBeenCalled();
  });

  it("confirms the knock-on impact before saving and generating, and saves nothing when cancelled", async () => {
    const user = userEvent.setup();
    // 服务端此刻仍判资产图为最新；改了描述，保存后它就会过期
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({
      stale: false,
      storyboards: 2,
      videos: 1,
      derivatives: 0,
    });
    const { onGenerate } = renderGallery();
    const sheet = await openAsset(user, "林夕");
    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，佩剑");

    await user.click(within(sheet).getByRole("button", { name: "保存并生成" }));
    let confirm = await screen.findByRole("alertdialog", { name: "重新生成这张资产图？" });
    expect(await within(confirm).findByText("重新生成后，用到它的 2 张分镜图、1 段视频会转为过期。")).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "取消" }));
    expect(API.updateCharacter).not.toHaveBeenCalled();
    expect(onGenerate).not.toHaveBeenCalled();

    await user.click(within(sheet).getByRole("button", { name: "保存并生成" }));
    confirm = await screen.findByRole("alertdialog", { name: "重新生成这张资产图？" });
    await user.click(await within(confirm).findByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith("林夕"));
    expect(API.updateCharacter).toHaveBeenCalledWith("demo", "林夕", { description: "白衣少女，佩剑" });
    expect(vi.mocked(API.updateCharacter).mock.invocationCallOrder[0]).toBeLessThan(
      onGenerate.mock.invocationCallOrder[0],
    );
  });

  it("saves the unsaved description before previewing the prompt", async () => {
    const user = userEvent.setup();
    const preview = vi.spyOn(API, "previewAssetPrompt").mockResolvedValue({
      text: "Style: Anime\n白衣少女，佩剑",
      unavailable: null,
      is_text_form: true,
      warnings: [],
    });
    renderGallery();
    const sheet = await openAsset(user, "林夕");
    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，佩剑");

    await user.click(within(sheet).getByRole("button", { name: "保存并预览" }));

    expect(await screen.findByText(/白衣少女，佩剑/, { selector: "pre" })).toBeInTheDocument();
    expect(API.updateCharacter).toHaveBeenCalledWith("demo", "林夕", { description: "白衣少女，佩剑" });
    expect(preview).toHaveBeenCalledWith("demo", "character", "林夕", "白衣少女，佩剑", expect.anything());
  });

  it.each([
    {
      type: "character" as const,
      name: "林夕",
      edit: async (user: ReturnType<typeof userEvent.setup>, sheet: HTMLElement) => {
        await user.type(within(sheet).getByRole("textbox", { name: "声音风格" }), "而坚定");
      },
      call: () => expect(API.updateCharacter).toHaveBeenCalledWith("demo", "林夕", { voice_style: "清冷而坚定" }),
    },
    {
      type: "scene" as const,
      name: "庭院",
      edit: async (user: ReturnType<typeof userEvent.setup>, sheet: HTMLElement) => {
        await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "月下庭院");
      },
      call: () => expect(API.updateProjectScene).toHaveBeenCalledWith("demo", "庭院", { description: "月下庭院" }),
    },
    {
      type: "prop" as const,
      name: "玉佩",
      edit: async (user: ReturnType<typeof userEvent.setup>, sheet: HTMLElement) => {
        await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，刻有云纹");
      },
      call: () =>
        expect(API.updateProjectProp).toHaveBeenCalledWith("demo", "玉佩", { description: "温润白玉，刻有云纹" }),
    },
    {
      type: "product" as const,
      name: "保温杯",
      edit: async (user: ReturnType<typeof userEvent.setup>, sheet: HTMLElement) => {
        await user.type(within(sheet).getByRole("textbox", { name: "品牌" }), "家");
        await user.type(within(sheet).getByRole("textbox", { name: "卖点" }), "{Enter}{Enter}一键开盖");
      },
      call: () =>
        expect(API.updateProjectProduct).toHaveBeenCalledWith("demo", "保温杯", {
          brand: "暖暖家",
          selling_points: ["保温 12 小时", "一键开盖"],
        }),
    },
  ])("edits a $type in the same editor and saves only the changed fields", async ({ type, name, edit, call }) => {
    const user = userEvent.setup();
    renderGallery(type);
    const sheet = await openAsset(user, name);

    await edit(user, sheet);
    await user.click(within(sheet).getByRole("button", { name: "保存" }));

    await waitFor(call);
    expect(await within(sheet).findByText("已保存")).toBeInTheDocument();
  });

  it("creates an asset from the blank editor and switches to its details", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "addCharacter").mockImplementation((_p, name, description, voiceStyle) => {
      server.characters[name] = { description, voice_style: voiceStyle };
      return Promise.resolve({ success: true });
    });
    renderGallery();

    await user.click(screen.getByRole("button", { name: "添加角色" }));
    const form = await screen.findByRole("dialog", { name: "添加角色" });
    expect(within(form).getByRole("button", { name: "创建" })).toBeDisabled();
    await user.type(within(form).getByRole("textbox", { name: "名称" }), " 阿青 ");
    await user.type(within(form).getByRole("textbox", { name: "描述" }), "青衣少年");
    await user.click(within(form).getByRole("button", { name: "创建" }));

    const sheet = await screen.findByRole("dialog", { name: "阿青" });
    expect(API.addCharacter).toHaveBeenCalledWith("demo", "阿青", "青衣少年", "");
    expect(within(sheet).getByRole("textbox", { name: "描述" })).toHaveValue("青衣少年");
  });

  it("keeps the create form open and explains why when creating fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "addProjectProduct").mockRejectedValue(new Error("商品「保温杯」已存在"));
    renderGallery("product");

    await user.click(screen.getByRole("button", { name: "添加商品" }));
    const form = await screen.findByRole("dialog", { name: "添加商品" });
    await user.type(within(form).getByRole("textbox", { name: "名称" }), "保温杯");
    await user.click(within(form).getByRole("button", { name: "创建" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("创建失败：商品「保温杯」已存在");
    expect(screen.getByRole("dialog", { name: "添加商品" })).toBeInTheDocument();
  });

  it("keeps unsaved edits visible when the asset is removed elsewhere, and closes once they are discarded", async () => {
    const user = userEvent.setup();
    renderGallery();
    const sheet = await openAsset(user, "林夕");
    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，佩剑");

    // Agent 删除了这个角色，推送后的项目数据里已经没有它
    const { 林夕: _removed, ...rest } = server.characters;
    server.characters = rest;
    act(() => useProjectsStore.setState({ currentProjectData: structuredClone(server) }));

    expect(screen.getByRole("dialog", { name: "林夕" })).toBeInTheDocument();
    expect(within(sheet).getByRole("textbox", { name: "描述" })).toHaveValue("白衣少女，佩剑");
    expect(within(sheet).getByText(/这个资产已被删除或改名/)).toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "林夕" })).not.toBeInTheDocument());
  });

  it("stays on the asset with its unsaved edits after renaming it", async () => {
    const user = userEvent.setup();
    const result = (dryRun: boolean): AssetRenameResult => ({
      success: true,
      dry_run: dryRun,
      old_name: "林夕",
      new_name: "林汐",
      episodes: 0,
      references: 0,
      files: 1,
    });
    vi.spyOn(API, "renameProjectAsset")
      .mockResolvedValueOnce(result(true))
      .mockImplementationOnce(() => {
        const { 林夕: renamed, ...rest } = server.characters;
        server.characters = { ...rest, 林汐: renamed };
        return Promise.resolve(result(false));
      });
    renderGallery();
    const sheet = await openAsset(user, "林夕");
    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，佩剑");

    await user.click(within(sheet).getByRole("button", { name: "重命名" }));
    const input = within(sheet).getByRole("textbox", { name: "重命名" });
    await user.clear(input);
    await user.type(input, "林汐{Enter}");
    const confirm = await screen.findByRole("alertdialog", { name: "重命名「林夕」？" });
    await user.click(within(confirm).getByRole("button", { name: "重命名" }));

    const renamed = await screen.findByRole("dialog", { name: "林汐" });
    expect(within(renamed).getByRole("textbox", { name: "描述" })).toHaveValue("白衣少女，佩剑");
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("cancels only the rename when Escape is pressed in the name input", async () => {
    const user = userEvent.setup();
    renderGallery();
    const sheet = await openAsset(user, "林夕");

    await user.click(within(sheet).getByRole("button", { name: "重命名" }));
    await user.keyboard("{Escape}");

    expect(within(sheet).queryByRole("textbox", { name: "重命名" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "林夕" })).toBeInTheDocument();
  });

  it("uploads a character's reference image right away", async () => {
    const user = userEvent.setup();
    const upload = vi.spyOn(API, "uploadFile").mockImplementation(() => {
      server.characters.苏白 = { ...server.characters.苏白, reference_image: "characters/refs/苏白.png" };
      return Promise.resolve({ success: true });
    });
    renderGallery();
    const sheet = await openAsset(user, "苏白");

    const file = new File(["png"], "ref.png", { type: "image/png" });
    await user.upload(within(sheet).getByLabelText("上传原图"), file);

    await waitFor(() => expect(upload).toHaveBeenCalledWith("demo", "character_ref", file, "苏白"));
    expect(await within(sheet).findByRole("button", { name: "查看大图：「苏白」的原图" })).toBeInTheDocument();
    expect(within(sheet).queryByText("未保存的修改")).not.toBeInTheDocument();
  });

  it("collapses reference audio while the project binds voices by prompt", async () => {
    const user = userEvent.setup();
    renderGallery();
    const sheet = await openAsset(user, "林夕");

    const toggle = within(sheet).getByRole("button", { name: "可选：参考音频" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(within(sheet).getByText(/参考音频不会生效/)).toBeInTheDocument();
  });

  it("confirms before deleting reference audio and rejects the deletion once the character became busy", async () => {
    const user = userEvent.setup();
    server = makeProject({ character_voice_binding: "reference_audio" });
    server.characters.林夕.reference_audio = "characters/refs_audio/林夕.wav";
    useProjectsStore.setState({ currentProjectData: structuredClone(server) });
    const remove = vi.spyOn(API, "deleteCharacterReferenceAudio").mockResolvedValue({ success: true } as never);
    renderGallery();
    const sheet = await openAsset(user, "林夕");

    await user.click(within(sheet).getByRole("button", { name: "删除音频样本" }));
    let confirm = await screen.findByRole("alertdialog", { name: "删除参考音频？" });
    // 打开确认框之后，角色被 Agent 入队占用
    act(() =>
      useTasksStore.setState({
        tasks: [makeTask({ project_name: "demo", task_type: "character", media_type: "image", resource_id: "林夕", status: "running" })],
      }),
    );
    await user.click(within(confirm).getByRole("button", { name: "删除" }));
    expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法删除音频样本");
    expect(remove).not.toHaveBeenCalled();

    act(() => useTasksStore.setState({ tasks: [] }));
    await user.click(within(sheet).getByRole("button", { name: "删除音频样本" }));
    confirm = await screen.findByRole("alertdialog", { name: "删除参考音频？" });
    await user.click(within(confirm).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("demo", "林夕"));
  });

  it("disables generation until the asset has a description", async () => {
    const user = userEvent.setup();
    renderGallery("scene");
    const sheet = await openAsset(user, "庭院");

    expect(within(sheet).getByRole("button", { name: "生成资产图" })).toBeDisabled();
    expect(within(sheet).getByText("需要先填写描述")).toBeInTheDocument();

    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "月下庭院");
    expect(within(sheet).getByRole("button", { name: "保存并生成" })).toBeEnabled();
  });

  it("shows the content without any write entry when read-only", async () => {
    const user = userEvent.setup();
    renderGallery("character", { readOnly: true });
    const sheet = await openAsset(user, "林夕");

    expect(within(sheet).getByRole("textbox", { name: "描述" })).toHaveAttribute("readonly");
    expect(within(sheet).getByRole("textbox", { name: "声音风格" })).toHaveAttribute("readonly");
    expect(within(sheet).queryByRole("button", { name: /生成|上传|重命名|保存|预览|添加|衍生/ })).not.toBeInTheDocument();
  });
});
