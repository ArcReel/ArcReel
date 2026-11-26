import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import { seedTasks } from "@/test/task-state";
import type { TaskProgress } from "@/types";
import { TaskChips } from "./TaskChips";

describe("TaskChips", () => {
  beforeEach(() => {
    seedTasks([]);
  });

  it("renders live text task phases, elapsed time, and 15s hint on controllable clock", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-11-01T12:00:12Z"));
      const task = makeTask({
        task_id: "t-plan-1",
        project_name: "demo",
        task_type: "text_drama_script_plan",
        media_type: "text",
        resource_id: "episode-1",
        status: "running",
        started_at: "2026-11-01T12:00:00Z",
      });
      seedTasks([task]);

      render(
        <TaskChips
          tasks={[
            {
              task_id: task.task_id,
              task_type: task.task_type,
              unit_id: "episode-1",
              status: "running",
            },
          ]}
        />,
      );

      expect(screen.getByText(/生成中…/)).toBeInTheDocument();
      expect(screen.getByLabelText(/已运行.*12/)).toBeInTheDocument();
      const update = (event: TaskProgress) => act(() => useTasksStore.getState().receiveProgress(event));
      update({ task_id: task.task_id, seq: 1, phase: "waiting", model: "Gemini" });
      expect(screen.getByText("正在等待 Gemini 响应…")).toBeInTheDocument();
      update({ task_id: task.task_id, seq: 2, phase: "receiving", thinking: true, characters: 0 });
      expect(screen.getByText("模型思考中…")).toBeInTheDocument();
      update({ task_id: task.task_id, seq: 3, phase: "receiving", thinking: false, characters: 180 });
      expect(screen.getByText("正在接收生成内容 · 已接收约 180 字…")).toBeInTheDocument();
      update({ task_id: task.task_id, seq: 4, phase: "retrying", attempt: 2, max_attempts: 3, error: "503" });
      expect(screen.getByText("正在重试（第 2/3 次尝试）…")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(3_000));
      expect(screen.queryByText(/长文本通常需要数十秒至数分钟/)).not.toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1_000));
      expect(screen.getByText(/长文本通常需要数十秒至数分钟/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders the honest wait-timeout message when a text task fails with text_wait_timeout", () => {
    const task = makeTask({
      task_id: "t-timeout-1",
      project_name: "demo",
      task_type: "text_episode_script",
      media_type: "text",
      resource_id: "episode-1",
      status: "failed",
      error_code: "text_wait_timeout",
      error_message: null,
    });
    seedTasks([task]);

    render(
      <TaskChips
        tasks={[
          {
            task_id: task.task_id,
            task_type: task.task_type,
            unit_id: "episode-1",
            status: "failed",
          },
        ]}
      />,
    );

    expect(
      screen.getByText("本地已停止等待，但供应商仍可能继续完成请求并计费。确认结果后再重试"),
    ).toBeInTheDocument();
  });
});
