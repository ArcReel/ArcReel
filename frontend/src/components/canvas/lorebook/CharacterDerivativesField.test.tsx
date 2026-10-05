import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { createDeferred } from "@/test/deferred";
import { makeTask } from "@/test/factories";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { Character, CharacterDerivative, ProjectData } from "@/types";
import { AssetGallery } from "./AssetGallery";

const SHEET_PATH = "characters/derivatives/林夕/战斗装.png";

function makeProject(): ProjectData {
  return {
    title: "Demo",
    content_mode: "drama",
    style: "Anime",
    episodes: [],
    characters: {
      林夕: {
        description: "白衣少女",
        character_sheet: "characters/林夕.png",
        derivatives: {
          战斗装: { description: "换上黑色重甲", character_sheet: SHEET_PATH, referenced: true },
          便装: { description: "布衣", referenced: false },
        },
      },
      苏白: { description: "黑衣剑客" },
    },
    scenes: {},
    props: {},
    products: {},
  };
}

let server: ProjectData;

function derivativesOf(name: string): Record<string, CharacterDerivative> {
  const character = server.characters[name] as Character;
  character.derivatives ??= {};
  return character.derivatives;
}

function Harness() {
  const project = useProjectsStore((s) => s.currentProjectData);
  return (
    <AssetGallery
      projectName="demo"
      assetType="character"
      title="角色"
      assets={project?.characters ?? {}}
      readOnly={false}
      onGenerate={vi.fn()}
    />
  );
}

async function openCharacter(user: ReturnType<typeof userEvent.setup>) {
  render(
    <Router hook={memoryLocation({ path: "/" }).hook}>
      <LeaveGuardProvider>
        <Harness />
      </LeaveGuardProvider>
    </Router>,
  );
  await user.click(screen.getByRole("button", { name: "林夕" }));
  return screen.findByRole("dialog", { name: "林夕" });
}

function rowOf(sheet: HTMLElement, name: string) {
  const heading = within(sheet).getByRole("heading", { name });
  return heading.closest("li") as HTMLElement;
}

describe("角色详情的衍生区块", () => {
  beforeEach(() => {
    server = makeProject();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: structuredClone(server) });
    vi.spyOn(API, "getProject").mockImplementation(() => Promise.resolve({ project: structuredClone(server), scripts: {} }));
    vi.spyOn(API, "getAssetSheetStatus").mockResolvedValue({ assets: [] });
    vi.spyOn(API, "getCharacterDerivativeSheets").mockImplementation(() =>
      Promise.resolve({
        success: true,
        derivatives: Object.fromEntries(
          Object.entries(derivativesOf("林夕")).map(([name, d]) => [name, { ...d, stale: false }]),
        ),
      }),
    );
    vi.spyOn(API, "updateCharacter").mockImplementation((_p, name, patch) => {
      Object.assign(server.characters[name], patch);
      return Promise.resolve({ success: true });
    });
    vi.spyOn(API, "updateCharacterDerivative").mockImplementation((_p, _c, name, description) => {
      derivativesOf("林夕")[name].description = description;
      return Promise.resolve({ success: true });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  it("copies a derivative's reference tag and shows whether the script uses it", async () => {
    const user = userEvent.setup();
    const sheet = await openCharacter(user);

    const armor = rowOf(sheet, "战斗装");
    expect(within(armor).getByText("脚本中已引用")).toBeInTheDocument();
    expect(within(rowOf(sheet, "便装")).getByText("脚本中尚未引用")).toBeInTheDocument();

    await user.click(within(armor).getByRole("button", { name: "复制引用记号 @[林夕/战斗装]" }));
    expect(await navigator.clipboard.readText()).toBe("@[林夕/战斗装]");
  });

  it("saves one derivative's appearance change from its own row", async () => {
    const user = userEvent.setup();
    const sheet = await openCharacter(user);
    const armor = rowOf(sheet, "战斗装");

    await user.type(within(armor).getByRole("textbox", { name: "「战斗装」的外观变化" }), "，披红斗篷");
    expect(within(armor).getByText("有未保存的修改")).toBeInTheDocument();
    expect(within(rowOf(sheet, "便装")).queryByText("有未保存的修改")).not.toBeInTheDocument();
    await user.click(within(armor).getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(API.updateCharacterDerivative).toHaveBeenCalledWith("demo", "林夕", "战斗装", "换上黑色重甲，披红斗篷"),
    );
    expect(API.updateCharacter).not.toHaveBeenCalled();
    await waitFor(() => expect(within(armor).queryByText("有未保存的修改")).not.toBeInTheDocument());
  });

  it("asks before closing the sheet over an unsaved appearance change", async () => {
    const user = userEvent.setup();
    const sheet = await openCharacter(user);
    await user.type(within(rowOf(sheet, "便装")).getByRole("textbox", { name: "「便装」的外观变化" }), "，草鞋");

    await user.click(within(sheet).getByRole("button", { name: "关闭" }));

    const leave = await screen.findByRole("alertdialog", { name: "「林夕/便装」有未保存的修改" });
    await user.click(within(leave).getByRole("button", { name: "继续编辑" }));
    expect(screen.getByRole("dialog", { name: "林夕" })).toBeInTheDocument();
    expect(within(sheet).getByRole("textbox", { name: "「便装」的外观变化" })).toHaveValue("布衣，草鞋");
  });

  it("saves both the character and the derivative before switching to the next character", async () => {
    const user = userEvent.setup();
    const sheet = await openCharacter(user);
    await user.type(within(sheet).getByRole("textbox", { name: "描述" }), "，佩剑");
    await user.type(within(rowOf(sheet, "战斗装")).getByRole("textbox", { name: "「战斗装」的外观变化" }), "，披红斗篷");

    await user.click(within(sheet).getByRole("button", { name: "下一个" }));
    const leave = await screen.findByRole("alertdialog");
    await user.click(within(leave).getByRole("button", { name: "保存并切换" }));

    expect(await screen.findByRole("dialog", { name: "苏白" })).toBeInTheDocument();
    expect(API.updateCharacter).toHaveBeenCalledWith("demo", "林夕", { description: "白衣少女，佩剑" });
    expect(API.updateCharacterDerivative).toHaveBeenCalledWith("demo", "林夕", "战斗装", "换上黑色重甲，披红斗篷");
  });

  it("keeps the unsaved appearance change after renaming the derivative", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "renameCharacterDerivative").mockImplementation((_p, _c, from, to) => {
      const table = derivativesOf("林夕");
      table[to] = table[from];
      delete table[from];
      return Promise.resolve({ success: true } as never);
    });
    const sheet = await openCharacter(user);
    await user.type(within(rowOf(sheet, "战斗装")).getByRole("textbox", { name: "「战斗装」的外观变化" }), "，披红斗篷");

    await user.click(within(rowOf(sheet, "战斗装")).getByRole("button", { name: "「战斗装」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "重命名衍生" }));
    const input = await within(sheet).findByRole("textbox", { name: "「战斗装」的新名称" });
    await user.clear(input);
    await user.type(input, "铠甲{Enter}");

    const renamed = await within(sheet).findByRole("heading", { name: "铠甲" });
    expect(API.renameCharacterDerivative).toHaveBeenCalledWith("demo", "林夕", "战斗装", "铠甲");
    expect(within(renamed.closest("li") as HTMLElement).getByRole("textbox", { name: "「铠甲」的外观变化" })).toHaveValue(
      "换上黑色重甲，披红斗篷",
    );

    // 旧名随后被新衍生复用：两行各自显示，不因沿用的行标识撞在一起
    derivativesOf("林夕").战斗装 = { description: "换回便装" };
    await act(() => useProjectsStore.getState().refreshProject("demo"));
    expect(within(rowOf(sheet, "战斗装")).getByRole("textbox", { name: "「战斗装」的外观变化" })).toHaveValue("换回便装");
    expect(within(rowOf(sheet, "铠甲")).getByRole("textbox", { name: "「铠甲」的外观变化" })).toHaveValue(
      "换上黑色重甲，披红斗篷",
    );
  });

  it("follows the stored NFC name when the new derivative name was typed decomposed", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "renameCharacterDerivative").mockImplementation((_p, _c, from, to) => {
      // 后端按 strip + NFC 规范化后落盘
      const table = derivativesOf("林夕");
      table[to.trim().normalize("NFC")] = table[from];
      delete table[from];
      return Promise.resolve({ success: true } as never);
    });
    const sheet = await openCharacter(user);
    await user.type(within(rowOf(sheet, "战斗装")).getByRole("textbox", { name: "「战斗装」的外观变化" }), "，披红斗篷");

    await user.click(within(rowOf(sheet, "战斗装")).getByRole("button", { name: "「战斗装」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "重命名衍生" }));
    const input = await within(sheet).findByRole("textbox", { name: "「战斗装」的新名称" });
    await user.clear(input);
    await user.type(input, "Gia\u0301p{Enter}");

    const renamed = await within(sheet).findByRole("heading", { name: "Giáp" });
    expect(within(renamed.closest("li") as HTMLElement).getByRole("textbox", { name: "「Giáp」的外观变化" })).toHaveValue(
      "换上黑色重甲，披红斗篷",
    );
    expect(within(sheet).queryByText("此衍生已被删除或改名。未保存的修改仍保留，保存或放弃后采用当前状态。")).not.toBeInTheDocument();
  });

  it.each(["delete", "rename"] as const)("retains an unsaved row after an external %s until it is discarded", async (change) => {
    const user = userEvent.setup();
    const sheet = await openCharacter(user);
    await user.type(within(rowOf(sheet, "战斗装")).getByRole("textbox", { name: "「战斗装」的外观变化" }), "，披红斗篷");
    const table = derivativesOf("林夕");
    if (change === "rename") table.铠甲 = table.战斗装;
    delete table.战斗装;
    await act(() => useProjectsStore.getState().refreshProject("demo"));

    const held = rowOf(sheet, "战斗装");
    expect(within(held).getByRole("textbox", { name: "「战斗装」的外观变化" })).toHaveValue("换上黑色重甲，披红斗篷");
    expect(within(held).getByText("此衍生已被删除或改名。未保存的修改仍保留，保存或放弃后采用当前状态。")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    vi.mocked(API.updateCharacterDerivative).mockRejectedValueOnce(new Error("衍生不存在"));
    await user.click(within(held).getByRole("button", { name: "保存" }));
    expect(await within(held).findByRole("alert")).toHaveTextContent("衍生不存在");
    expect(within(held).getByRole("textbox")).toHaveValue("换上黑色重甲，披红斗篷");

    await user.click(within(held).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(within(sheet).queryByRole("heading", { name: "战斗装" })).not.toBeInTheDocument());
    expect(within(sheet).queryAllByRole("heading", { name: "铠甲" })).toHaveLength(change === "rename" ? 1 : 0);
  });

  it("protects the character and keeps the version panel open during a derivative restore", async () => {
    const user = userEvent.setup();
    const pending = createDeferred<{ success: boolean }>();
    vi.spyOn(API, "getVersions").mockResolvedValue({
      resource_type: "character_derivatives", resource_id: "林夕/战斗装", current_version: 2,
      versions: [{ version: 1, filename: "v1.png", created_at: "2026-10-01", file_size: 1, is_current: false }],
    });
    vi.spyOn(API, "restoreVersion").mockReturnValue(pending.promise);
    const sheet = await openCharacter(user);
    await user.click(within(rowOf(sheet, "战斗装")).getByRole("button", { name: "「战斗装」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "版本历史" }));
    await user.click(await screen.findByRole("button", { name: "v1" }));
    await user.click(screen.getByRole("button", { name: "切换到此版本" }));
    expect(within(sheet).getByRole("button", { name: "重命名", hidden: true })).toBeDisabled();
    await user.keyboard("{Escape}{Escape}");
    expect(screen.getByText("历史版本")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "林夕" })).toBeInTheDocument();
    await act(() => pending.resolve({ success: true }));
    expect(within(sheet).getByRole("button", { name: "重命名" })).toBeEnabled();
  });

  it("adds a derivative from the section header without a success toast", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "addCharacterDerivative").mockImplementation((_p, _c, name, description) => {
      derivativesOf("林夕")[name] = { description };
      return Promise.resolve({ success: true } as never);
    });
    const sheet = await openCharacter(user);

    await user.click(within(sheet).getByRole("button", { name: "新增衍生" }));
    await user.type(await screen.findByRole("textbox", { name: "衍生名" }), "睡衣");
    await user.type(screen.getByRole("textbox", { name: "外观变化" }), "换上白色睡衣");
    await user.click(screen.getByRole("button", { name: "添加" }));

    expect(await within(sheet).findByRole("heading", { name: "睡衣" })).toBeInTheDocument();
    expect(API.addCharacterDerivative).toHaveBeenCalledWith("demo", "林夕", "睡衣", "换上白色睡衣");
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("deletes a derivative after a confirmation that names it and its script reference", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "deleteCharacterDerivative").mockImplementation((_p, _c, name) => {
      delete derivativesOf("林夕")[name];
      return Promise.resolve({ success: true } as never);
    });
    const sheet = await openCharacter(user);

    await user.click(within(rowOf(sheet, "战斗装")).getByRole("button", { name: "「战斗装」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "删除" }));
    const confirm = await screen.findByRole("alertdialog", { name: "删除衍生「战斗装」？" });
    expect(confirm).toHaveTextContent("脚本中仍在用 @[林夕/战斗装] 引用它。");
    await user.click(within(confirm).getByRole("button", { name: "删除" }));

    await waitFor(() => expect(within(sheet).queryByRole("heading", { name: "战斗装" })).not.toBeInTheDocument());
    expect(API.deleteCharacterDerivative).toHaveBeenCalledWith("demo", "林夕", "战斗装");
  });

  it("disables the confirmed deletion while the character becomes busy after the confirmation opens", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "deleteCharacterDerivative").mockImplementation((_p, _c, name) => {
      delete derivativesOf("林夕")[name];
      return Promise.resolve({ success: true } as never);
    });
    const sheet = await openCharacter(user);

    await user.click(within(rowOf(sheet, "战斗装")).getByRole("button", { name: "「战斗装」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "删除" }));
    const confirm = await screen.findByRole("alertdialog", { name: "删除衍生「战斗装」？" });
    // 打开确认框之后，角色被 Agent 入队占用
    act(() =>
      useTasksStore.setState({
        tasks: [makeTask({ project_name: "demo", task_type: "character", media_type: "image", resource_id: "林夕", status: "running" })],
      }),
    );
    expect(within(confirm).getByRole("button", { name: "删除" })).toBeDisabled();

    act(() => useTasksStore.setState({ tasks: [] }));
    await user.click(within(confirm).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(API.deleteCharacterDerivative).toHaveBeenCalledWith("demo", "林夕", "战斗装"));
  });

  it("drops the row's unsaved appearance change together with the derivative it deletes", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "deleteCharacterDerivative").mockImplementation((_p, _c, name) => {
      delete derivativesOf("林夕")[name];
      return Promise.resolve({ success: true } as never);
    });
    const sheet = await openCharacter(user);
    await user.type(within(rowOf(sheet, "战斗装")).getByRole("textbox", { name: "「战斗装」的外观变化" }), "，披红斗篷");

    await user.click(within(rowOf(sheet, "战斗装")).getByRole("button", { name: "「战斗装」的更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "删除" }));
    const confirm = await screen.findByRole("alertdialog", { name: "删除衍生「战斗装」？" });
    await user.click(within(confirm).getByRole("button", { name: "删除" }));

    await waitFor(() => expect(within(sheet).queryByRole("heading", { name: "战斗装" })).not.toBeInTheDocument());
    expect(within(sheet).queryByText("此衍生已被删除或改名。未保存的修改仍保留，保存或放弃后采用当前状态。")).not.toBeInTheDocument();
  });

  it("closes only the image view on Escape, keeping the sheet open", async () => {
    const user = userEvent.setup();
    const sheet = await openCharacter(user);

    await user.click(within(sheet).getByRole("button", { name: "查看大图：衍生「林夕/战斗装」的资产图" }));
    expect(await screen.findByRole("dialog", { name: "林夕/战斗装" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "林夕/战斗装" })).not.toBeInTheDocument());
    expect(screen.getByRole("dialog", { name: "林夕" })).toBeInTheDocument();
  });
});
