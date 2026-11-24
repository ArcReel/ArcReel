import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Asset } from "@/types/asset";
import { AddToLibraryDialog, type LibraryImportPreview } from "./AddToLibraryDialog";

const PREVIEW: LibraryImportPreview = {
  description: "白衣少年，腰间佩剑",
  voiceStyle: "清亮少年音",
  hasReferenceAudio: true,
  sheetPath: null,
  derivativeCount: 2,
};

const EXISTING: Asset = {
  id: "lib-1",
  type: "character",
  name: "Hero",
  description: "",
  voice_style: "",
  image_path: null,
  audio_path: null,
  source_project: null,
  updated_at: null,
  derivatives: [],
};

function dialogFor(busy: boolean) {
  return (
    <AddToLibraryDialog
      resourceType="character"
      resourceId="Hero"
      projectName="demo"
      preview={PREVIEW}
      busy={busy}
      open
      onOpenChange={() => {}}
    />
  );
}

function renderDialog(busy = false) {
  return render(dialogFor(busy));
}

describe("AddToLibraryDialog", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("previews the project asset read-only and submits only the edited name", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "findAssetByName").mockResolvedValue({ asset: null });
    const addSpy = vi.spyOn(API, "addAssetFromProject").mockResolvedValue({ asset: EXISTING });
    renderDialog();

    const dialog = await screen.findByRole("dialog", { name: "加入资产库：Hero" });
    expect(within(dialog).getByText("白衣少年，腰间佩剑")).toBeInTheDocument();
    expect(within(dialog).getByText("清亮少年音")).toBeInTheDocument();
    expect(within(dialog).getByText("附带参考音频")).toBeInTheDocument();
    expect(within(dialog).getByText("2 个衍生")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("textbox")).toEqual([within(dialog).getByLabelText("名称")]);

    const name = within(dialog).getByLabelText("名称");
    await user.clear(name);
    await user.type(name, "少年剑客");
    await user.click(within(dialog).getByRole("button", { name: "加入资产库" }));

    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith({
        project_name: "demo",
        resource_type: "character",
        resource_id: "Hero",
        override_name: "少年剑客",
        overwrite: false,
      }),
    );
  });

  it("offers overwrite only while the name matches an existing library asset", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "findAssetByName").mockImplementation(async (_type, name) => ({
      asset: name === "Hero" ? EXISTING : null,
    }));
    const addSpy = vi.spyOn(API, "addAssetFromProject").mockResolvedValue({ asset: EXISTING });
    renderDialog();

    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "覆盖已有" });
    expect(within(dialog).getByRole("button", { name: "加入资产库" })).toBeDisabled();

    await user.type(within(dialog).getByLabelText("名称"), "2");
    await waitFor(() => expect(within(dialog).queryByRole("button", { name: "覆盖已有" })).not.toBeInTheDocument());
    await user.type(within(dialog).getByLabelText("名称"), "{Backspace}");
    await user.click(await within(dialog).findByRole("button", { name: "覆盖已有" }));

    await waitFor(() => expect(addSpy).toHaveBeenCalledWith(expect.objectContaining({ overwrite: true })));
  });

  it("asks for explicit overwrite when a same-name asset appears after the lookup", async () => {
    const user = userEvent.setup();
    const writes: unknown[] = [];
    const fetchMock = vi.fn(async (_url: string, options?: RequestInit) => {
      if (options?.method !== "POST") {
        return Response.json({ asset: null });
      }
      if (typeof options.body === "string") writes.push(JSON.parse(options.body));
      return writes.length === 1
        ? Response.json({ detail: { message: "角色已存在", existing: EXISTING } }, { status: 409 })
        : Response.json({ asset: EXISTING });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = renderDialog();

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await user.click(within(dialog).getByRole("button", { name: "加入资产库" }));
    const overwrite = await within(dialog).findByRole("button", { name: "覆盖已有" });
    expect(writes).toEqual([{
      project_name: "demo", resource_type: "character", resource_id: "Hero", overwrite: false,
    }]);

    expect(overwrite).toBeEnabled();
    rerender(dialogFor(true));
    expect(overwrite).toBeDisabled();
    await user.click(overwrite);
    expect(writes).toHaveLength(1);

    rerender(dialogFor(false));
    expect(overwrite).toBeEnabled();
    await user.click(overwrite);
    expect(useAppStore.getState().toast?.text).toBe("「Hero」已加入资产库");
    expect(writes[1]).toEqual({
      project_name: "demo", resource_type: "character", resource_id: "Hero", overwrite: true,
      overwrite_asset_id: "lib-1",
    });
  });

  it("refuses to submit when the resource becomes busy while the dialog is open", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "findAssetByName").mockResolvedValue({ asset: null });
    const addSpy = vi.spyOn(API, "addAssetFromProject").mockResolvedValue({ asset: EXISTING });
    const { rerender } = renderDialog(false);

    const dialog = await screen.findByRole("dialog");
    rerender(dialogFor(true));
    await user.click(within(dialog).getByRole("button", { name: "加入资产库" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("生成或编辑进行中，暂无法加入资产库");
    expect(addSpy).not.toHaveBeenCalled();
  });
});
