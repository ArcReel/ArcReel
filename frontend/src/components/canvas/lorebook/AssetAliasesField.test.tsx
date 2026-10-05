import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import { AssetAliasesField } from "./AssetAliasesField";

describe("AssetAliasesField", () => {
  beforeEach(() => useAppStore.setState(useAppStore.getInitialState(), true));
  afterEach(() => { vi.restoreAllMocks(); useTasksStore.setState({ tasks: [], optimisticActive: new Set() }); });

  function stubSave() {
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    return vi.spyOn(API, "updateProjectScene").mockResolvedValue({ success: true });
  }

  it("adds an alias only through the explicit add form and saves the whole list", async () => {
    const user = userEvent.setup();
    const update = stubSave();
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头"]} />);

    await user.click(screen.getByRole("button", { name: "添加别名" }));
    await user.type(await screen.findByLabelText("别名"), " 老槐树下 ");
    expect(update).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("p", "村口", { aliases: ["村头", "老槐树下"] }));
    await waitFor(() => expect(screen.queryByLabelText("别名")).not.toBeInTheDocument());
  });

  it("removes an alias immediately and restores it from the undo action", async () => {
    const user = userEvent.setup();
    const update = stubSave();
    const { rerender } = render(
      <AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头", "槐树下"]} />,
    );

    await user.click(screen.getByRole("button", { name: "删除别名「村头」" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("p", "村口", { aliases: ["槐树下"] }));
    // 刷新后的项目数据里已经没有这个别名
    rerender(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["槐树下"]} />);
    const toast = useAppStore.getState().toast;
    expect(toast?.text).toBe("已删除别名「村头」");
    toast?.action?.onClick();
    await waitFor(() =>
      expect(update).toHaveBeenLastCalledWith("p", "村口", { aliases: ["槐树下", "村头"] }),
    );
  });

  it("warns instead of offering an undo it could not honour when the refresh after removing fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("failed");
    const update = vi.spyOn(API, "updateProjectScene").mockResolvedValue({ success: true });
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头", "槐树下"]} />);

    await user.click(screen.getByRole("button", { name: "删除别名「村头」" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
        tone: "warning",
      }),
    );
    expect(useAppStore.getState().toast?.action).toBeUndefined();
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("offers no undo when the project was switched away before the removal synced", async () => {
    const user = userEvent.setup();
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("cancelled");
    vi.spyOn(API, "updateProjectScene").mockResolvedValue({ success: true });
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头", "槐树下"]} />);

    await user.click(screen.getByRole("button", { name: "删除别名「村头」" }));

    await waitFor(() => expect(API.updateProjectScene).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: "删除别名「村头」" })).toBeEnabled());
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("retains the add form when a task occupies the asset before submission", async () => {
    const user = userEvent.setup();
    const update = stubSave();
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={[]} />);
    await user.click(screen.getByRole("button", { name: "添加别名" }));
    await user.type(await screen.findByLabelText("别名"), "槐树下");
    useTasksStore.setState({ tasks: [makeTask({ project_name: "p", task_type: "scene", resource_id: "村口", status: "running" })] });
    await user.click(screen.getByRole("button", { name: "添加" }));
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByLabelText("别名")).toHaveValue("槐树下");
    expect(useAppStore.getState().toast?.text).toBe("资产图正在生成或修改，请等它结束后再操作");
  });

  it("only lists aliases when read-only", () => {
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头"]} readOnly />);

    expect(screen.getByText("村头")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
