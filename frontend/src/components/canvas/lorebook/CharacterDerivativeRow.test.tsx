import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";
import type { CharacterDerivativeStatus, ProjectData, ProviderAssetImportResult } from "@/types";
import { CharacterDerivativeRow, type CharacterDerivativeRowProps } from "./CharacterDerivativeRow";

const SHEET_PATH = "characters/derivatives/阿岚/战斗装.png";
const IMAGE_ALT = "衍生「阿岚/战斗装」的资产图";

function renderRow(status: CharacterDerivativeStatus | undefined, overrides: Partial<CharacterDerivativeRowProps> = {}) {
  render(
    <ul>
      <CharacterDerivativeRow
        projectName="demo"
        characterName="阿岚"
        name="战斗装"
        derivative={{ description: status?.description ?? "换上黑色重甲", character_sheet: status?.character_sheet }}
        status={status}
        ownerHasSheet
        readOnly={false}
        busy={false}
        onView={vi.fn()}
        onRenamed={vi.fn()}
        onSheetsChanged={vi.fn()}
        {...overrides}
      />
    </ul>,
  );
}

async function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: "「战斗装」的更多操作" }));
  await screen.findByRole("menu");
}

describe("CharacterDerivativeRow", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("marks the sheet outdated once the base look moved on", () => {
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: true });

    expect(screen.getByAltText(IMAGE_ALT)).toHaveAttribute("src", expect.stringContaining(SHEET_PATH));
    expect(screen.getByText("已过期")).toBeInTheDocument();
  });

  it("refuses to generate before the character has a sheet of its own but still offers an upload", async () => {
    renderRow({ description: "换上黑色重甲", character_sheet: "", stale: false }, { ownerHasSheet: false });

    expect(screen.getByText("生成衍生图需要本体资产图，也可以直接上传衍生图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成衍生图" })).toBeDisabled();
    await openMenu();
    expect(screen.getByRole("menuitem", { name: "上传衍生图" })).toBeEnabled();
  });

  it("uploads the chosen file as the derivative sheet and refreshes the project and its fingerprint", async () => {
    const upload = vi.spyOn(API, "uploadCharacterDerivativeSheet").mockResolvedValue({
      success: true,
      path: SHEET_PATH,
      version: 2,
      asset_fingerprints: { [SHEET_PATH]: 42 },
    });
    const project = (character_sheet?: string): ProjectData => ({
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: { 阿岚: { description: "", derivatives: { 战斗装: { description: "换上黑色重甲", character_sheet } } } },
      scenes: {},
      props: {},
    });
    useProjectsStore.getState().setCurrentProject("demo", project());
    const getProject = vi.spyOn(API, "getProject").mockResolvedValue({ project: project(SHEET_PATH), scripts: {} });
    const onSheetsChanged = vi.fn();
    renderRow({ description: "换上黑色重甲", character_sheet: "", stale: false }, { onSheetsChanged });
    const file = new File(["png"], "armor.png", { type: "image/png" });

    fireEvent.change(screen.getByLabelText("上传「阿岚/战斗装」的衍生图"), { target: { files: [file] } });

    await waitFor(() => expect(onSheetsChanged).toHaveBeenCalled());
    expect(upload).toHaveBeenCalledWith("demo", "阿岚", "战斗装", file);
    expect(getProject).toHaveBeenCalledWith("demo", expect.anything());
    const state = useProjectsStore.getState();
    expect(state.currentProjectData?.characters["阿岚"].derivatives?.["战斗装"].character_sheet).toBe(SHEET_PATH);
    expect(state.assetFingerprints[SHEET_PATH]).toBe(42);
  });

  it("drops the upload while the base look is being generated", async () => {
    const upload = vi.spyOn(API, "uploadCharacterDerivativeSheet");
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character", resource_id: "阿岚" })],
      optimisticActive: new Set(),
    });
    const file = new File(["png"], "armor.png", { type: "image/png" });

    fireEvent.change(screen.getByLabelText("上传「阿岚/战斗装」的衍生图"), { target: { files: [file] } });

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法修改衍生"));
    expect(upload).not.toHaveBeenCalled();
  });

  it("treats a registered but unreadable derivative image as pending", () => {
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false, artifact_status: "missing" });

    expect(screen.queryByAltText(IMAGE_ALT)).not.toBeInTheDocument();
    expect(screen.getByText("待生成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成衍生图" })).toBeEnabled();
  });

  it("shows the registered derivative image while its status is still loading", () => {
    renderRow(undefined, { derivative: { description: "换上黑色重甲", character_sheet: SHEET_PATH } });

    expect(screen.getByAltText(IMAGE_ALT)).toHaveAttribute("src", expect.stringContaining(SHEET_PATH));
    expect(screen.queryByText("待生成")).not.toBeInTheDocument();
  });

  it("enqueues a regeneration addressed by the compound resource id after confirming the impact", async () => {
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({ stale: true, storyboards: 2, videos: 1, derivatives: 0 });
    const spy = vi
      .spyOn(API, "generateCharacterDerivative")
      .mockResolvedValue({ success: true, task_id: "task-1", deduped: false, message: "已提交" });
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: true });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    const confirm = await screen.findByRole("alertdialog");
    // Open the dialog as loading first; impact summary arrives later.
    await waitFor(() => expect(confirm).toHaveTextContent("2 张分镜图、1 段视频"));
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("demo", "阿岚", "战斗装"));
  });

  it("shows a 640px thumbnail in the row and opens the original image in the viewer", () => {
    useProjectsStore.setState({ assetFingerprints: { [SHEET_PATH]: 7 } });
    const onView = vi.fn();
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false }, { onView });

    const thumb = screen.getByAltText(IMAGE_ALT);
    expect(thumb).toHaveAttribute("src", `/api/v1/files/demo/${SHEET_PATH}?v=7&w=640`);
    expect(thumb).toHaveAttribute("loading", "lazy");

    fireEvent.click(screen.getByRole("button", { name: `查看大图：${IMAGE_ALT}` }));
    expect(onView).toHaveBeenCalledWith(expect.objectContaining({ src: `/api/v1/files/demo/${SHEET_PATH}?v=7` }));
  });

  it("retries the thumbnail once the sheet is replaced", async () => {
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });

    fireEvent.error(screen.getByAltText(IMAGE_ALT));
    await waitFor(() => expect(screen.queryByAltText(IMAGE_ALT)).not.toBeInTheDocument());

    act(() => useProjectsStore.getState().updateAssetFingerprints({ [SHEET_PATH]: 99 }));

    await waitFor(() => expect(screen.getByAltText(IMAGE_ALT)).toHaveAttribute("src", expect.stringContaining("v=99")));
  });

  it("rechecks the busy slot at submit time and drops the click", async () => {
    const spy = vi.spyOn(API, "generateCharacterDerivative");
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({ stale: false, storyboards: 0, videos: 0, derivatives: 0 });
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });
    // Another generation occupies the base after rendering: a fresh submit-time recheck must block.
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character", resource_id: "阿岚" })],
      optimisticActive: new Set(),
    });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法修改衍生"));
    expect(spy).not.toHaveBeenCalled();
  });
  it("selects a provider library image as the derivative sheet and keeps the row busy until it lands", async () => {
    vi.spyOn(API, "listAssetLibraryAccounts").mockResolvedValue({
      sites: [{ id: "volcengine", regions: ["cn-beijing"] }],
      default_project_name: "default",
      accounts: [
        {
          id: 7,
          site: "volcengine",
          name: "主账号",
          region: "cn-beijing",
          project_name: "film",
          access_key_id_masked: "TEST…0001",
          secret_access_key_masked: "TEST…0002",
          created_at: "2026-11-01T00:00:00Z",
        },
      ],
    });
    vi.spyOn(API, "listProviderAssets").mockResolvedValue({
      items: [{ id: "asset-1", name: "重甲正面", asset_type: "Image", status: "active", error_code: null, created_at: null }],
      total: 1,
      page: 1,
      page_size: 20,
    });
    const pending = createDeferred<ProviderAssetImportResult>();
    const importAsset = vi.spyOn(API, "importProviderAsset").mockReturnValue(pending.promise);
    const getProject = vi.spyOn(API, "getProject").mockResolvedValue({
      project: {
        title: "Demo",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: { 阿岚: { description: "", derivatives: { 战斗装: { description: "换上黑色重甲", character_sheet: SHEET_PATH } } } },
        scenes: {},
        props: {},
      },
      scripts: {},
    });
    const onSheetsChanged = vi.fn();
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false }, { onSheetsChanged });

    await openMenu();
    expect(screen.getByRole("menuitem", { name: "上传到供应商素材库" })).toBeEnabled();
    fireEvent.click(screen.getByRole("menuitem", { name: "从供应商素材库选择" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择 重甲正面" }));

    expect(importAsset).toHaveBeenCalledWith("demo", {
      resource_type: "character_derivatives",
      resource_id: "阿岚/战斗装",
      account_id: 7,
      asset_id: "asset-1",
    });
    // Import in flight disables sibling writes for this derivative (the open dialog hides its row from the accessibility tree).
    expect(screen.getByRole("button", { name: "重新生成", hidden: true })).toBeDisabled();

    await act(async () =>
      pending.resolve({
        content_version: { id: "cv-2", version: 2, digest: "d" },
        link: {
          id: 11,
          content_version_id: "cv-2",
          version: 2,
          account_id: 7,
          scope_key: "scope-7",
          site: "volcengine",
          region: "cn-beijing",
          provider_project_name: "film",
          state: "active",
          selected: true,
          usable: true,
          provider_asset_id: "asset-1",
          error_code: null,
          error_message: null,
          check_error: null,
          polling: false,
          created_at: null,
          submitted_at: null,
          settled_at: null,
        },
      }),
    );

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("已导入为第 2 版"));
    expect(getProject).toHaveBeenCalledWith("demo", expect.anything());
    expect(onSheetsChanged).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "重新生成" })).toBeEnabled();
  });
});
