import { act, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import { seedTasks } from "@/test/task-state";
import type { TaskProgress } from "@/types";
import { PromptAuthoringButton } from "./PromptAuthoringButton";

it("shows the live phase and elapsed time for this episode", () => {
  vi.useFakeTimers();
  try {
    vi.setSystemTime(new Date("2026-11-01T12:00:08Z"));
    const task = makeTask({ resource_id: "episode-1", task_type: "text_episode_script", status: "running",
      started_at: "2026-11-01T12:00:00Z" });
    seedTasks([task]);
    render(<PromptAuthoringButton projectName="proj" episode={1} scope="pending" />);
    const update = (event: TaskProgress) => act(() => useTasksStore.getState().receiveProgress(event));
    const task_id = task.task_id;
    expect(screen.getByRole("button", { name: "编写提示词" })).toBeDisabled();
    expect(screen.getByLabelText(/已运行.*8/)).toBeInTheDocument();
    update({ task_id, seq: 1, phase: "waiting", model: "Demo" });
    expect(screen.getByText("正在等待 Demo 响应…")).toBeInTheDocument();
    update({ task_id, seq: 2, phase: "receiving", thinking: true, characters: 0 });
    expect(screen.getByText("模型思考中…")).toBeInTheDocument();
    update({ task_id, seq: 3, phase: "receiving", thinking: false, characters: 63 });
    expect(screen.getByText(/已接收约 63 字/)).toBeInTheDocument();
    update({ task_id, seq: 4, phase: "retrying", attempt: 2, max_attempts: 3, error: "RateLimitError" });
    expect(screen.getByText(/第 2\/3 次尝试/)).toBeInTheDocument();
    expect(screen.queryByText(/长文本通常需要数十秒至数分钟/)).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(7_000));
    expect(screen.queryByText(/长文本通常需要数十秒至数分钟/)).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByText(/长文本通常需要数十秒至数分钟/)).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});
