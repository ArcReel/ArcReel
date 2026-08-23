import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useRef } from "react";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import i18n from "@/i18n";

// 前端 finding #12 回归：cascade 取消的 task 在 HUD 渲染 cascade_label，
// user 取消的不渲染。验证 SSE 携带的 cancelled_by 字段在前端被正确分流。

function HostedTaskHud() {
  const anchorRef = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={anchorRef} data-testid="anchor" />
      <TaskHud anchorRef={anchorRef} />
    </div>
  );
}

function resetStores() {
  useAppStore.setState({ taskHudOpen: true });
  useTasksStore.setState({
    tasks: [],
    stats: { queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0 },
  });
}

describe("TaskHud cascade label", () => {
  afterEach(() => {
    cleanup();
    useAppStore.setState({ taskHudOpen: false });
    useTasksStore.setState({
    tasks: [],
    stats: { queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0 },
  });
  });

  it("renders cascade label when cancelled_by === 'cascade'", async () => {
    await i18n.changeLanguage("zh");
    resetStores();
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "cascade-1",
          status: "cancelled",
          cancelled_by: "cascade",
          task_type: "video",
          media_type: "video",
        }),
      ],
    });

    render(<HostedTaskHud />);
    const labels = await screen.findAllByText("级联");
    expect(labels.length).toBeGreaterThan(0);
  });

  it("does not render cascade label for user cancel", async () => {
    await i18n.changeLanguage("zh");
    resetStores();
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "user-1",
          status: "cancelled",
          cancelled_by: "user",
          task_type: "video",
          media_type: "video",
        }),
      ],
    });

    render(<HostedTaskHud />);
    expect(screen.queryByText("级联")).toBeNull();
  });

  it("renders English cascade label after locale switch", async () => {
    await i18n.changeLanguage("en");
    resetStores();
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "cascade-en",
          status: "cancelled",
          cancelled_by: "cascade",
          task_type: "video",
          media_type: "video",
        }),
      ],
    });

    render(<HostedTaskHud />);
    const labels = await screen.findAllByText("cascaded");
    expect(labels.length).toBeGreaterThan(0);
    await i18n.changeLanguage("zh");
  });
});
