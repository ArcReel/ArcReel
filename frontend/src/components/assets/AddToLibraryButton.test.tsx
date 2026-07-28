import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { AddToLibraryButton } from "./AddToLibraryButton";

describe("AddToLibraryButton", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "listAssets").mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rejects submit if the resource becomes busy while the import modal is open (issue #1159 round-11 Codex finding)", async () => {
    const addSpy = vi.spyOn(API, "addAssetFromProject").mockResolvedValue({} as never);

    const { rerender } = render(
      <AddToLibraryButton
        resourceType="character"
        resourceId="Hero"
        projectName="demo"
        initialDescription="hero desc"
        busy={false}
      />,
    );

    // 打开时资源未占用，弹窗正常打开
    fireEvent.click(screen.getByRole("button", { name: "加入资产库" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "确认入库" })).toBeInTheDocument();
    });

    // 弹窗打开期间，资源通过 SSE/另一个标签页进入生成或 image_edit 占用态
    rerender(
      <AddToLibraryButton
        resourceType="character"
        resourceId="Hero"
        projectName="demo"
        initialDescription="hero desc"
        busy
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "确认入库" }));

    await waitFor(() => {
      expect(useAppStore.getState().toast?.tone).toBe("error");
    });
    expect(addSpy).not.toHaveBeenCalled();
  });

  it("allows submit when the resource stays idle throughout", async () => {
    const addSpy = vi.spyOn(API, "addAssetFromProject").mockResolvedValue({} as never);

    render(
      <AddToLibraryButton
        resourceType="character"
        resourceId="Hero"
        projectName="demo"
        initialDescription="hero desc"
        busy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "加入资产库" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "确认入库" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "确认入库" }));

    await waitFor(() => {
      expect(addSpy).toHaveBeenCalledWith(
        expect.objectContaining({ project_name: "demo", resource_type: "character", resource_id: "Hero" }),
      );
    });
  });
});
