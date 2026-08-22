import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import { ProjectAssetDeleteButton } from "./ProjectAssetDeleteButton";

describe("ProjectAssetDeleteButton", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("confirms through the shared project asset endpoint, then reloads", async () => {
    const deleteSpy = vi.spyOn(API, "deleteProjectAsset").mockResolvedValue({ success: true } as never);
    const onReload = vi.fn().mockResolvedValue(true);
    render(
      <ProjectAssetDeleteButton
        projectName="demo"
        assetType="character"
        name="Hero"
        onReload={onReload}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除资产" }));
    expect(screen.getByRole("dialog", { name: "删除「Hero」？" })).toBeInTheDocument();
    expect(screen.getByText(/全局资产库内容不会被删除/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith("demo", "character", "Hero");
      expect(onReload).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancels without deleting", () => {
    const deleteSpy = vi.spyOn(API, "deleteProjectAsset");
    render(
      <ProjectAssetDeleteButton projectName="demo" assetType="scene" name="Temple" />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除资产" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("rechecks task occupancy when the dialog is confirmed", async () => {
    const deleteSpy = vi.spyOn(API, "deleteProjectAsset");
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(
      <ProjectAssetDeleteButton projectName="demo" assetType="prop" name="Sword" />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除资产" }));
    useTasksStore.setState({
      tasks: [
        makeTask({
          project_name: "demo",
          task_type: "prop",
          media_type: "image",
          resource_id: "Sword",
          status: "running",
        }),
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("生成或编辑进行中，暂无法删除该资产", "info");
    });
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reports API failure and keeps the card available for retry", async () => {
    vi.spyOn(API, "deleteProjectAsset").mockRejectedValue(new Error("network down"));
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(
      <ProjectAssetDeleteButton projectName="demo" assetType="scene" name="Temple" />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除资产" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("删除失败：network down", "error");
    });
    expect(screen.getByRole("dialog", { name: "删除「Temple」？" })).toBeInTheDocument();
  });
});
