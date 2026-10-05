import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { AssetSheetStatusRow, AssetSheetType, ProjectData } from "@/types";
import { AssetGallery } from "./AssetGallery";

interface Source {
  description: string;
  scene_sheet?: string;
}

const SCENES: Record<string, Source> = {
  庭院: { description: "阴森古朴", scene_sheet: "scenes/庭院.png" },
  书房: { description: "堆满古籍" },
  卧室: { description: "雕花木床", scene_sheet: "scenes/卧室.png" },
};

function row(name: string, status: AssetSheetStatusRow["status"], type: AssetSheetType = "scene"): AssetSheetStatusRow {
  return {
    unit_id: `${type}/${name}`,
    asset_type: type,
    name,
    derivative: null,
    status,
    description_missing: false,
    image_to_image: false,
  };
}

const libraryPreview = (source: Source) => ({ description: source.description, sheetPath: source.scene_sheet });

function renderGallery(overrides: Partial<Parameters<typeof AssetGallery<Source>>[0]> = {}) {
  return render(
    <AssetGallery<Source>
      projectName="demo"
      assetType="scene"
      title="场景"
      assets={SCENES}
      readOnly={false}
      onGenerate={vi.fn()}
      libraryPreview={libraryPreview}
      {...overrides}
    />,
  );
}

function cardNames() {
  const list = screen.getByRole("list", { name: "场景" });
  return within(list)
    .getAllByRole("article")
    .map((card) => card.id);
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: `「${name}」的更多操作` }));
  return screen.findByRole("menu");
}

describe("AssetGallery", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "getAssetSheetStatus").mockResolvedValue({
      assets: [row("庭院", "stale"), row("书房", "missing"), row("卧室", "current")],
    });
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    vi.restoreAllMocks();
  });

  it("narrows the grid to pending or stale sheets and marks each card", async () => {
    const user = userEvent.setup();
    renderGallery();
    expect(await within(screen.getByRole("article", { name: "庭院" })).findByText("已过期")).toBeInTheDocument();
    expect(cardNames()).toEqual(["scene-庭院", "scene-书房", "scene-卧室"]);

    await user.click(screen.getByRole("button", { name: /^待生成/ }));
    expect(cardNames()).toEqual(["scene-书房"]);

    await user.click(screen.getByRole("button", { name: /^已过期/ }));
    expect(cardNames()).toEqual(["scene-庭院"]);
  });

  it("opens the asset detail when the card is clicked", async () => {
    const user = userEvent.setup();
    renderGallery();

    await user.click(screen.getByRole("button", { name: "书房" }));

    const sheet = await screen.findByRole("dialog", { name: "书房" });
    expect(within(sheet).getByRole("textbox", { name: "描述" })).toHaveValue("堆满古籍");
  });

  it("collects the secondary actions in the card menu", async () => {
    const user = userEvent.setup();
    renderGallery();

    const menu = await openMenu(user, "庭院");

    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "查看大图",
      "重新生成资产图",
      "上传资产图",
      "局部修改",
      "版本历史",
      "加入资产库",
      "并入…",
      "删除",
    ]);
  });

  it("disables restoring a version once another task occupies the asset", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "scenes", resource_id: "庭院", current_version: 2,
      versions: [{ version: 1, filename: "v1.png", created_at: "2026-10-01", file_size: 1, is_current: false }],
    });
    renderGallery();
    await user.click(within(await openMenu(user, "庭院")).getByRole("menuitem", { name: "版本历史" }));
    await user.click(await screen.findByRole("button", { name: "v1" }));
    expect(screen.getByRole("button", { name: "切换到此版本" })).toBeEnabled();

    act(() => useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "scene", resource_id: "庭院", status: "running" })],
    }));

    expect(screen.getByRole("button", { name: "切换到此版本" })).toBeDisabled();
  });

  it("keeps library actions out of product cards", async () => {
    const user = userEvent.setup();
    renderGallery({ assetType: "product", title: "商品", libraryPreview: undefined });

    const menu = await openMenu(user, "庭院");

    expect(within(menu).queryByRole("menuitem", { name: "加入资产库" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "并入…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "从资产库选择" })).not.toBeInTheDocument();
  });

  it("offers only the image viewer when read-only", async () => {
    const user = userEvent.setup();
    renderGallery({ readOnly: true });

    const menu = await openMenu(user, "庭院");

    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["查看大图"]);
    expect(screen.queryByRole("button", { name: /添加场景/ })).not.toBeInTheDocument();
  });

  it("refuses to open a sheet-writing dialog once the asset became busy", async () => {
    const user = userEvent.setup();
    renderGallery();
    const menu = await openMenu(user, "庭院");
    // 菜单打开之后，该场景被 Agent 入队占用，菜单应即时禁用写入入口。
    act(() => useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "scene", media_type: "image", resource_id: "庭院", status: "running" })],
    }));

    const addToLibrary = within(menu).getByRole("menuitem", { name: "加入资产库" });
    expect(addToLibrary).toHaveAttribute("aria-disabled", "true");
    await user.click(addToLibrary);

    expect(screen.queryByRole("dialog", { name: /加入资产库/ })).not.toBeInTheDocument();
  });

  it("drops an upload chosen after the asset became busy", async () => {
    const uploadFile = vi.spyOn(API, "uploadFile").mockResolvedValue({ path: "x" } as never);
    renderGallery();
    const card = screen.getByRole("article", { name: "庭院" });
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "scene", media_type: "image", resource_id: "庭院", status: "running" })],
    });

    const file = new File(["sheet"], "scene.png", { type: "image/png" });
    fireEvent.change(within(card).getByLabelText("上传资产图", { selector: "input" }), { target: { files: [file] } });

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法上传资产图"));
    expect(uploadFile).not.toHaveBeenCalled();
  });

  describe("deleting an asset", () => {
    beforeEach(() => {
      const project = {
        title: "demo",
        scenes: SCENES,
        episodes: [
          { episode: 1, title: "开端" },
          { episode: 2, title: "夜访" },
        ],
      } as unknown as ProjectData;
      useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: project });
      vi.spyOn(API, "getProject").mockResolvedValue({ project, scripts: {} } as never);
    });

    afterEach(() => {
      useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    });

    it("lists the episodes that still reference the asset and offers merging instead", async () => {
      const user = userEvent.setup();
      const preview = vi.spyOn(API, "previewProjectAssetDeletion").mockResolvedValue({
        success: true,
        dry_run: true,
        name: "庭院",
        references: 5,
        episodes: [
          { episode: 1, references: 3 },
          { episode: 2, references: 2 },
        ],
      });
      const remove = vi.spyOn(API, "deleteProjectAsset");
      renderGallery();

      await user.click(within(await openMenu(user, "庭院")).getByRole("menuitem", { name: "删除" }));

      const dialog = await screen.findByRole("alertdialog", { name: "删除场景「庭院」？" });
      expect(
        await within(dialog).findByText("被「开端」等 2 集共 5 处引用，删除后这些分镜在生成时会被拦下，资产也无法恢复。"),
      ).toBeInTheDocument();
      expect(within(dialog).getByText("夜访")).toBeInTheDocument();
      expect(preview).toHaveBeenCalledWith("demo", "scene", "庭院", { signal: expect.any(AbortSignal) });

      await user.click(within(dialog).getByRole("button", { name: "改为并入…" }));

      expect(await screen.findByRole("alertdialog", { name: "把「庭院」并入另一个资产" })).toBeInTheDocument();
      expect(screen.queryByRole("alertdialog", { name: "删除场景「庭院」？" })).not.toBeInTheDocument();
      expect(remove).not.toHaveBeenCalled();
    });

    it("only warns that deletion is irreversible when nothing references the asset", async () => {
      const user = userEvent.setup();
      vi.spyOn(API, "previewProjectAssetDeletion").mockResolvedValue({
        success: true,
        dry_run: true,
        name: "书房",
        references: 0,
        episodes: [],
      });
      const remove = vi.spyOn(API, "deleteProjectAsset").mockResolvedValue({ success: true });
      renderGallery();

      await user.click(within(await openMenu(user, "书房")).getByRole("menuitem", { name: "删除" }));
      const dialog = await screen.findByRole("alertdialog", { name: "删除场景「书房」？" });
      expect(await within(dialog).findByText("删除后无法恢复。")).toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: "改为并入…" })).not.toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "删除" }));

      expect(remove).toHaveBeenCalledWith("demo", "scene", "书房");
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    });
  });
  describe("image viewer", () => {
    const version = (n: number, current: boolean) => ({
      version: n,
      filename: `庭院_v${n}.png`,
      created_at: "2026-10-01 12:00",
      file_size: 1,
      is_current: current,
      file_url: `/files/庭院_v${n}.png`,
    });

    beforeEach(() => {
      vi.spyOn(API, "getVersions").mockImplementation(async (_project, _type, name) => ({
        resource_type: "scenes",
        resource_id: name,
        current_version: 2,
        versions: [version(1, false), version(2, true)],
      }));
    });

    async function openViewer(user: ReturnType<typeof userEvent.setup>, name: string) {
      await user.click(within(await openMenu(user, name)).getByRole("menuitem", { name: "查看大图" }));
      const viewer = await screen.findByRole("dialog", { name });
      // 菜单关闭时会把焦点还给触发按钮，等查看器接过焦点再操作
      await waitFor(() => expect(viewer).toContainElement(document.activeElement as HTMLElement));
      return viewer;
    }

    it("steps through the filtered assets that have an image", async () => {
      const user = userEvent.setup();
      renderGallery();

      let viewer = await openViewer(user, "庭院");
      expect(within(viewer).getByText("1 / 2")).toBeInTheDocument();
      // 书房没有资产图，→ 直接跳到卧室
      await user.keyboard("{ArrowRight}");
      viewer = await screen.findByRole("dialog", { name: "卧室" });
      expect(within(viewer).getByText("2 / 2")).toBeInTheDocument();
      expect(within(viewer).getByRole("button", { name: "下一个" })).toBeDisabled();
      await user.keyboard("{ArrowLeft}");
      expect(await screen.findByRole("dialog", { name: "庭院" })).toBeInTheDocument();
      await user.keyboard("{Escape}");

      await user.click(screen.getByRole("button", { name: /^已过期/ }));
      viewer = await openViewer(user, "庭院");
      expect(within(viewer).getByText("1 / 1")).toBeInTheDocument();
      await user.keyboard("{ArrowRight}");
      expect(screen.getByRole("dialog", { name: "庭院" })).toBeInTheDocument();
    });

    it("stays closed after the viewed asset drops out and later comes back", async () => {
      const user = userEvent.setup();
      const { rerender } = renderGallery();
      await openViewer(user, "庭院");

      const withoutSheet = { ...SCENES, 庭院: { description: "阴森古朴" } };
      const gallery = (assets: Record<string, Source>) => (
        <AssetGallery<Source>
          projectName="demo"
          assetType="scene"
          title="场景"
          assets={assets}
          readOnly={false}
          onGenerate={vi.fn()}
          libraryPreview={libraryPreview}
        />
      );
      rerender(gallery(withoutSheet));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "庭院" })).not.toBeInTheDocument());
      rerender(gallery(SCENES));

      expect(screen.queryByRole("dialog", { name: "庭院" })).not.toBeInTheDocument();
    });

    it("only previews an earlier version until the restore is confirmed", async () => {
      const user = userEvent.setup();
      const restore = vi.spyOn(API, "restoreVersion").mockResolvedValue({ success: true });
      const onRestoreVersion = vi.fn();
      renderGallery({ onRestoreVersion });
      const viewer = await openViewer(user, "庭院");

      await user.click(await within(viewer).findByRole("button", { name: "第 1 版" }));

      expect(within(viewer).getByRole("img", { name: "「庭院」的资产图，第 1 版" })).toHaveAttribute("src", "/files/庭院_v1.png");
      expect(within(viewer).getByText("正在查看旧版本，当前是第 2 版")).toBeInTheDocument();
      expect(restore).not.toHaveBeenCalled();

      await user.click(within(viewer).getByRole("button", { name: "还原到此版本" }));
      let confirm = await screen.findByRole("alertdialog", { name: "还原到第 1 版？" });
      await user.click(within(confirm).getByRole("button", { name: "取消" }));
      expect(restore).not.toHaveBeenCalled();

      await user.click(within(viewer).getByRole("button", { name: "还原到此版本" }));
      confirm = await screen.findByRole("alertdialog", { name: "还原到第 1 版？" });
      await user.click(within(confirm).getByRole("button", { name: "还原" }));

      expect(restore).toHaveBeenCalledWith("demo", "scenes", "庭院", 1);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(onRestoreVersion).toHaveBeenCalled();
    });

    it("drops the previous image when an earlier version fails to load", async () => {
      const user = userEvent.setup();
      renderGallery();
      const viewer = await openViewer(user, "庭院");
      const current = within(viewer).getByRole("img", { name: "「庭院」的资产图，第 2 版" });
      // 大图区域（版本条另有缩略图）
      const stage = current.closest("span")?.parentElement as HTMLElement;
      fireEvent.load(current);

      await user.click(await within(viewer).findByRole("button", { name: "第 1 版" }));
      fireEvent.error(within(viewer).getByRole("img", { name: "「庭院」的资产图，第 1 版" }));

      // 第 1 版读不出来：显示占位，不能让第 2 版的画面冒充第 1 版
      expect(stage.querySelector("img")).toBeNull();
    });

    it("keeps the restore unavailable while the asset image is being generated", async () => {
      const user = userEvent.setup();
      renderGallery({ generatingNames: new Set(["庭院"]) });
      const viewer = await openViewer(user, "庭院");

      await user.click(await within(viewer).findByRole("button", { name: "第 1 版" }));

      const restore = within(viewer).getByRole("button", { name: "还原到此版本" });
      expect(restore).toBeDisabled();
      expect(restore).toHaveAccessibleDescription("资产图正在生成、上传或修改，完成后才能还原版本。");
    });

    it("opens the asset detail from the viewer", async () => {
      const user = userEvent.setup();
      renderGallery();
      const viewer = await openViewer(user, "卧室");

      await user.click(within(viewer).getByRole("button", { name: "编辑" }));

      const sheet = await screen.findByRole("dialog", { name: "卧室" });
      expect(within(sheet).getByRole("textbox", { name: "描述" })).toHaveValue("雕花木床");
    });
  });
});
