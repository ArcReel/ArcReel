import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterDerivativesButton } from "./CharacterDerivativesButton";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

const DERIVATIVES = {
  战斗装: { description: "换上黑色重甲", character_sheet: "" },
  便装: { description: "布衣", character_sheet: "" },
};

function renderButton(overrides: Partial<Parameters<typeof CharacterDerivativesButton>[0]> = {}) {
  const onReload = vi.fn();
  render(
    <CharacterDerivativesButton
      projectName="demo"
      characterName="阿岚"
      derivatives={DERIVATIVES}
      onReload={onReload}
      {...overrides}
    />,
  );
  return { onReload };
}

function openPanel() {
  fireEvent.click(screen.getByRole("button", { name: "衍生（2）" }));
}

describe("CharacterDerivativesButton", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("lists each derivative with its copyable reference tag", () => {
    renderButton();
    openPanel();

    expect(screen.getByText("@[阿岚/战斗装]")).toBeInTheDocument();
    expect(screen.getByText("@[阿岚/便装]")).toBeInTheDocument();
    expect(screen.getByLabelText("「战斗装」的外观变化")).toHaveValue("换上黑色重甲");
  });

  it("shows whether each derivative is referenced by the script", () => {
    renderButton({
      derivatives: {
        战斗装: { description: "换上黑色重甲", character_sheet: "", referenced: true },
        便装: { description: "布衣", character_sheet: "", referenced: false },
      },
    });
    openPanel();

    expect(screen.getByText("脚本中已引用")).toBeInTheDocument();
    expect(screen.getByText("脚本中尚未引用")).toBeInTheDocument();
  });

  it("stays silent about the reference state when the API did not report it", () => {
    renderButton();
    openPanel();

    expect(screen.queryByText("脚本中已引用")).not.toBeInTheDocument();
    expect(screen.queryByText("脚本中尚未引用")).not.toBeInTheDocument();
  });

  it("registers a new derivative and refreshes the project", async () => {
    const addSpy = vi.spyOn(API, "addCharacterDerivative").mockResolvedValue({ success: true });
    const { onReload } = renderButton();
    openPanel();

    fireEvent.change(screen.getByLabelText("衍生名"), { target: { value: "  兽化  " } });
    fireEvent.change(screen.getByLabelText("外观变化"), { target: { value: "长出兽耳与尾巴" } });
    fireEvent.click(screen.getByRole("button", { name: "新增衍生" }));

    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith("demo", "阿岚", "兽化", "长出兽耳与尾巴"),
    );
    await waitFor(() => expect(onReload).toHaveBeenCalled());
  });

  it("saves an edited description only after it differs from the stored one", async () => {
    const updateSpy = vi.spyOn(API, "updateCharacterDerivative").mockResolvedValue({ success: true });
    renderButton();
    openPanel();

    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("「战斗装」的外观变化"), {
      target: { value: "换上银色轻甲" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith("demo", "阿岚", "战斗装", "换上银色轻甲"),
    );
  });

  it("renames a derivative from the inline editor", async () => {
    const renameSpy = vi.spyOn(API, "renameCharacterDerivative").mockResolvedValue({ success: true });
    renderButton();
    openPanel();

    fireEvent.click(screen.getAllByRole("button", { name: "重命名衍生" })[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "重命名衍生" }), {
      target: { value: "铠甲" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认重命名" }));

    await waitFor(() => expect(renameSpy).toHaveBeenCalledWith("demo", "阿岚", "战斗装", "铠甲"));
  });

  it("deletes a derivative after the confirmation is accepted", async () => {
    const deleteSpy = vi.spyOn(API, "deleteCharacterDerivative").mockResolvedValue({ success: true });
    renderButton();
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "删除衍生「便装」" }));
    expect(deleteSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("demo", "阿岚", "便装"));
  });

  it("keeps the derivative when the deletion is cancelled", () => {
    const deleteSpy = vi.spyOn(API, "deleteCharacterDerivative").mockResolvedValue({ success: true });
    renderButton();
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "删除衍生「便装」" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.getByText("@[阿岚/便装]")).toBeInTheDocument();
  });

  it("refuses to write while the character is occupied by a queued task", async () => {
    const addSpy = vi.spyOn(API, "addCharacterDerivative").mockResolvedValue({ success: true });
    renderButton();
    openPanel();

    fireEvent.change(screen.getByLabelText("衍生名"), { target: { value: "兽化" } });
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character", resource_id: "阿岚" })],
      optimisticActive: new Set(),
    });
    fireEvent.click(screen.getByRole("button", { name: "新增衍生" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法修改衍生"),
    );
    expect(addSpy).not.toHaveBeenCalled();
  });

  it("does not open while a sibling control on the card is writing", () => {
    renderButton({ busy: true });

    const trigger = screen.getByRole("button", { name: "衍生（2）" });
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByLabelText("衍生名")).not.toBeInTheDocument();
  });
});
