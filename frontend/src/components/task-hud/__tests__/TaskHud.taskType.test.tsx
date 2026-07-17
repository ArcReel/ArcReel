import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useRef } from "react";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import i18n from "@/i18n";

// issue #1218：任务 HUD 的 task_type 标签本地化。已知类型经 i18n key 映射显示；
// 词表外的未知类型机械兜底显示原始串（不做语义猜测映射）。

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

describe("TaskHud task_type label", () => {
  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("zh");
    useAppStore.setState({ taskHudOpen: false });
    useTasksStore.setState({
      tasks: [],
      stats: { queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0 },
    });
  });

  it("renders localized label for a known task_type (zh)", async () => {
    await i18n.changeLanguage("zh");
    resetStores();
    useTasksStore.setState({
      tasks: [
        makeTask({ task_id: "known-zh", task_type: "image_edit", media_type: "image" }),
      ],
    });

    render(<HostedTaskHud />);
    expect(await screen.findByText("图片编辑")).toBeInTheDocument();
    expect(screen.queryByText("image_edit")).toBeNull();
  });

  it("renders localized label for a known task_type (en)", async () => {
    await i18n.changeLanguage("en");
    resetStores();
    useTasksStore.setState({
      tasks: [makeTask({ task_id: "known-en", task_type: "storyboard", media_type: "image" })],
    });

    render(<HostedTaskHud />);
    expect(await screen.findByText("Storyboard")).toBeInTheDocument();
  });

  it("falls back to the raw string for an unknown task_type", async () => {
    await i18n.changeLanguage("zh");
    resetStores();
    useTasksStore.setState({
      tasks: [
        makeTask({ task_id: "unknown-1", task_type: "some_future_type", media_type: "image" }),
      ],
    });

    render(<HostedTaskHud />);
    expect(await screen.findByText("some_future_type")).toBeInTheDocument();
  });
});
