import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";

import { StartBlankScriptButton } from "./StartBlankScriptButton";

describe("StartBlankScriptButton", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  it("warns when the blank script is created but the project refresh fails", async () => {
    const start = vi.spyOn(API, "startBlankScript").mockResolvedValue({ success: true, script_file: "scripts/episode_2.json" });
    vi.spyOn(API, "getProject").mockRejectedValue(new Error("offline"));
    render(<StartBlankScriptButton projectName="demo" episode={2} discardsPlan={false} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "从空白开始" }));

    expect(start).toHaveBeenCalledWith("demo", 2);
    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({
        text: "操作已完成，但页面数据刷新失败，请手动刷新查看最新状态",
        tone: "warning",
      }),
    );
  });
});
