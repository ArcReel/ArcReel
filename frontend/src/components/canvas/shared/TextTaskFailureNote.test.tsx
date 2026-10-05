import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { TaskItem } from "@/types";

import { TextTaskFailureNote } from "./TextTaskFailureNote";

const TRUNCATED = "文本模型 my-llm 的输出超出了最大输出长度，内容不完整";

function failed(overrides: Partial<TaskItem>): TaskItem {
  return makeTask({ project_name: "demo", status: "failed", queued_at: "2026-10-01T10:00:00Z", ...overrides });
}

function truncated(overrides: Partial<TaskItem>, custom: boolean): TaskItem {
  return failed({
    error_message: TRUNCATED,
    error_code: "text_output_truncated",
    error_params: { provider_id: custom ? "custom-7" : "gemini-aistudio", model: "my-llm", custom_model: custom },
    ...overrides,
  });
}

function renderNote(episode = 1, ad?: { hasScript: boolean }) {
  const location = memoryLocation({ path: "/episodes/1", record: true });
  render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <TextTaskFailureNote projectName="demo" episode={episode} isAd={ad !== undefined} hasScript={ad?.hasScript ?? true} />
    </Router>,
  );
  return location;
}

describe("TextTaskFailureNote", () => {
  beforeEach(() => {
    useTasksStore.setState(useTasksStore.getInitialState(), true);
  });

  it.each([
    ["text_narration_script_plan", "episode-1", "上一次 AI 规划脚本失败"],
    ["text_episode_script", "episode-1", "上一次编写提示词失败"],
    ["text_draft_repair", "episode-1-narration_script_plan", "上一次 AI 修复失败"],
  ])("sends a truncated custom model of %s to its entry in settings", (taskType, resourceId, lead) => {
    useTasksStore.setState({ tasks: [truncated({ task_type: taskType, resource_id: resourceId }, true)] });
    const location = renderNote();

    expect(screen.getByRole("alert")).toHaveTextContent(`${lead}：${TRUNCATED}`);
    fireEvent.click(screen.getByRole("link", { name: "去登记最大输出长度" }));

    expect(location.history.at(-1)).toBe("/app/settings?section=providers&custom=7&model=my-llm");
  });

  it("asks to switch models when a built-in model is truncated", () => {
    useTasksStore.setState({ tasks: [truncated({ task_type: "text_episode_script", resource_id: "episode-1" }, false)] });
    renderNote();

    expect(screen.getByText(/请在设置中换一个文本模型后再试/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "去登记最大输出长度" })).not.toBeInTheDocument();
  });

  it("shows only the latest text task of this episode, until it is dismissed", () => {
    useTasksStore.setState({
      tasks: [
        failed({ task_id: "old", task_type: "text_episode_script", resource_id: "episode-1", error_message: "旧的失败" }),
        failed({
          task_id: "other-episode",
          task_type: "text_episode_script",
          resource_id: "episode-12",
          queued_at: "2026-10-01T12:00:00Z",
        }),
        failed({
          task_id: "new",
          task_type: "text_drama_script_plan",
          resource_id: "episode-1",
          queued_at: "2026-10-01T11:00:00Z",
          error_message: "源文读取失败",
        }),
      ],
    });
    renderNote();

    expect(screen.getByRole("alert")).toHaveTextContent("上一次 AI 规划脚本失败：源文读取失败");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays away once the latest text task of this episode has not failed", () => {
    useTasksStore.setState({
      tasks: [
        failed({ task_type: "text_episode_script", resource_id: "episode-1" }),
        makeTask({
          project_name: "demo",
          task_type: "text_episode_script",
          resource_id: "episode-1",
          status: "running",
          queued_at: "2026-10-01T11:00:00Z",
        }),
      ],
    });
    renderNote();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    ["没有正式脚本时脚本文本任务一律是整份生成", { hasScript: false }, {}],
    ["有正式脚本时按违约失败的标记认出整份生成", { hasScript: true }, { error_code: "ad_script_rejected" }],
    ["有正式脚本时按整份重做的标记认出整份生成", { hasScript: true }, { payload: { regenerate: true } }],
  ])("labels an ad whole-script generation failure: %s", (_name, ad, overrides) => {
    useTasksStore.setState({
      tasks: [
        failed({
          task_type: "text_episode_script",
          resource_id: "episode-1",
          error_message: "AI 生成的脚本不合规",
          ...overrides,
        }),
      ],
    });
    renderNote(1, ad);

    expect(screen.getByRole("alert")).toHaveTextContent("上一次 AI 生成脚本失败：AI 生成的脚本不合规");
  });

  it("keeps prompt authoring failures of an ad episode with a script as prompt authoring", () => {
    useTasksStore.setState({
      tasks: [failed({ task_type: "text_episode_script", resource_id: "episode-1", error_message: "模型超时" })],
    });
    renderNote(1, { hasScript: true });

    expect(screen.getByRole("alert")).toHaveTextContent("上一次编写提示词失败：模型超时");
  });
});
