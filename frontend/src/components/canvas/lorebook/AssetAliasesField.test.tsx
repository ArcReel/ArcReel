import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { AssetAliasesField } from "./AssetAliasesField";

describe("AssetAliasesField", () => {
  beforeEach(() => useAppStore.setState(useAppStore.getInitialState(), true));
  afterEach(() => vi.restoreAllMocks());

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

  it("only lists aliases when read-only", () => {
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头"]} readOnly />);

    expect(screen.getByText("村头")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
