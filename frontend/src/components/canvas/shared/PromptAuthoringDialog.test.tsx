import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { API, ApiRequestError } from "@/api";
import { usePromptAuthoringStore } from "@/stores/prompt-authoring-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { EpisodeScript, PromptOverwrite } from "@/types";
import { PromptAuthoringHost } from "./PromptAuthoringDialog";

const ACCEPTED = { batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.authorPrompts>>;

// 条目 ID 里的数字与位置错开（E1S04 是分镜 2），界面只按位置称呼条目。
const SCRIPT = {
  episode: 1,
  segments: [
    { segment_id: "E1S01", novel_text: "城门洞开", image_prompt: "", video_prompt: "", pending_authoring: true },
    { segment_id: "E1S04", novel_text: "雨夜无人", image_prompt: "雨夜", video_prompt: "推近" },
    { segment_id: "E1S07", novel_text: "灯笼熄灭", image_prompt: "", video_prompt: "", pending_authoring: true },
  ],
};

function openDialog(scope: "pending" | "current" | "custom" = "pending") {
  usePromptAuthoringStore.getState().open({ projectName: "proj", episode: 1, scope });
  render(<PromptAuthoringHost projectName="proj" episode={1} script={SCRIPT} />);
}

beforeEach(() => {
  useTasksStore.getState().setTasks([]);
  useProjectsStore.setState({ currentScripts: { "episode_1.json": SCRIPT as unknown as EpisodeScript } });
});

afterEach(() => {
  vi.restoreAllMocks();
  useProjectsStore.setState({ currentScripts: {} });
  usePromptAuthoringStore.getState().close();
});

describe("编写提示词", () => {
  it("自选多条只提交勾选的条目，按剧本顺序", async () => {
    const user = userEvent.setup();
    const submit = vi.spyOn(API, "authorPrompts").mockResolvedValue(ACCEPTED);
    openDialog("custom");

    const dialog = await screen.findByRole("dialog", { name: "编写提示词" });
    const list = within(dialog).getByRole("list", { name: "自选多条" });
    // 默认勾选待编写的分镜 1、分镜 3；再勾上分镜 2，去掉分镜 1。条目按位置与正文摘录指认
    expect(within(list).queryByText(/E1S0\d/)).not.toBeInTheDocument();
    await user.click(within(list).getByRole("checkbox", { name: /^分镜 2雨夜无人/ }));
    await user.click(within(list).getByRole("checkbox", { name: /^分镜 1城门洞开/ }));
    await user.click(within(dialog).getByRole("button", { name: "AI 编写" }));

    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith("proj", 1, {
        entry_ids: ["E1S04", "E1S07"],
        rewrite: false,
        instructions: null,
        overwrite_revision: null,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("重写会覆盖已有提示词时先确认，确认后带着清单版本重新提交", async () => {
    const user = userEvent.setup();
    const overwrite: PromptOverwrite = { revision: "rev-7", entries: [{ id: "E1S04", fields: ["image_prompt"] }], text: "将覆盖 E1S04 的分镜图与视频提示词。" };
    const submit = vi
      .spyOn(API, "authorPrompts")
      .mockRejectedValueOnce(new ApiRequestError("需要确认覆盖", { prompt_overwrite: overwrite }, 409))
      .mockResolvedValueOnce(ACCEPTED);
    openDialog();

    const dialog = await screen.findByRole("dialog", { name: "编写提示词" });
    await user.click(within(dialog).getByRole("checkbox", { name: "覆盖已有提示词" }));
    await user.click(within(dialog).getByRole("button", { name: "AI 重写" }));

    const confirm = await screen.findByRole("alertdialog", { name: "确认覆盖已有提示词" });
    expect(within(confirm).getByText("将覆盖 分镜 2 的分镜图与视频提示词。")).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "AI 重写" }));

    await waitFor(() =>
      expect(submit).toHaveBeenLastCalledWith("proj", 1, {
        entry_ids: null,
        rewrite: true,
        instructions: null,
        overwrite_revision: "rev-7",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });
});
