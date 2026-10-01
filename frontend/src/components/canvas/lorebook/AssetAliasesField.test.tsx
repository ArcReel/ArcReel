import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { AssetAliasesField } from "./AssetAliasesField";

describe("AssetAliasesField", () => {
  afterEach(() => vi.restoreAllMocks());

  function stubSave() {
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
    return vi.spyOn(API, "updateProjectScene").mockResolvedValue({ success: true });
  }

  it("adds an alias on Enter and saves the whole list", async () => {
    const update = stubSave();
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头"]} />);

    const input = screen.getByLabelText("别名");
    fireEvent.change(input, { target: { value: " 老槐树下 " } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(update).toHaveBeenCalledWith("p", "村口", { aliases: ["村头", "老槐树下"] }));
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("removes an alias", async () => {
    const update = stubSave();
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头", "槐树下"]} />);

    fireEvent.click(screen.getByLabelText("删除别名「村头」"));

    await waitFor(() => expect(update).toHaveBeenCalledWith("p", "村口", { aliases: ["槐树下"] }));
  });

  it("only lists aliases when read-only", () => {
    render(<AssetAliasesField projectName="p" name="村口" assetType="scene" aliases={["村头"]} readOnly />);

    expect(screen.getByText("村头")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("删除别名「村头」")).not.toBeInTheDocument();
  });
});
