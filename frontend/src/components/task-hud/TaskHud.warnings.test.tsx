import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useRef } from "react";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import i18n from "@/i18n";

// 后端 `_localize_task` 已把 result.warnings 渲染成当前语言的字符串数组，
// HUD 只负责标识 + 展开；`result` 在类型上是 Record<string, unknown>，非字符串条目须被丢弃。

const emptyStats = {
  queued: 0,
  running: 0,
  cancelling: 0,
  succeeded: 0,
  failed: 0,
  cancelled: 0,
  total: 0,
};

function HostedTaskHud() {
  const anchorRef = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={anchorRef} data-testid="anchor" />
      <TaskHud anchorRef={anchorRef} />
    </div>
  );
}

const WARNING_TEXT = "剧本编排 7s 不在 sora 的时长档位内，已按 8s 生成，成片长于剧本编排";

function warnedTask(warnings: unknown) {
  return makeTask({
    task_id: "warned-1",
    status: "succeeded",
    task_type: "reference_video",
    media_type: "video",
    resource_id: "WARNED1",
    result: { version: 1, warnings } as Record<string, unknown>,
  });
}

describe("TaskHud generation warnings", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh");
    useAppStore.setState({ taskHudOpen: true });
    useTasksStore.setState({ tasks: [], stats: emptyStats });
  });

  afterEach(() => {
    cleanup();
    useAppStore.setState({ taskHudOpen: false });
    useTasksStore.setState({ tasks: [], stats: emptyStats });
  });

  it("marks a succeeded task that carries warnings and expands the texts", async () => {
    const user = userEvent.setup();
    useTasksStore.setState({ tasks: [warnedTask([WARNING_TEXT])] });
    render(<HostedTaskHud />);

    const row = screen.getByText("WARNED1").closest('[role="button"]');
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(WARNING_TEXT)).toBeNull();

    await user.click(row as HTMLElement);

    expect(row?.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(WARNING_TEXT)).toBeTruthy();
  });

  it("renders every warning as its own list item", async () => {
    const user = userEvent.setup();
    useTasksStore.setState({
      tasks: [warnedTask([WARNING_TEXT, "「小美」（character）缺少可用参考图，本次生成已跳过该参考"])],
    });
    render(<HostedTaskHud />);

    await user.click(screen.getByText("WARNED1").closest('[role="button"]') as HTMLElement);

    expect(screen.getAllByRole("listitem").length).toBe(2);
  });

  it("leaves a succeeded task without warnings non-expandable", () => {
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "clean-1",
          status: "succeeded",
          media_type: "video",
          resource_id: "CLEAN1",
          result: { version: 1, warnings: [] },
        }),
      ],
    });
    render(<HostedTaskHud />);

    expect(screen.getByText("CLEAN1").closest('[role="button"]')).toBeNull();
  });

  it("drops non-string warning entries instead of rendering them", () => {
    useTasksStore.setState({
      tasks: [warnedTask([{ key: "ref_duration_rounded_up", params: {} }])],
    });
    render(<HostedTaskHud />);

    expect(screen.getByText("WARNED1").closest('[role="button"]')).toBeNull();
    expect(screen.queryByText(/object Object/)).toBeNull();
  });

  it("keeps the failure tint on a failed task that carries no error_message", () => {
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "failed-nomsg",
          status: "failed",
          media_type: "video",
          resource_id: "NOMSG",
          error_message: null,
        }),
      ],
    });
    render(<HostedTaskHud />);

    // 无详情可展开，但底色仍须标出失败，否则这行与排队行无从区分。
    const row = screen.getByText("NOMSG").parentElement as HTMLElement;
    expect(row.closest('[role="button"]')).toBeNull();
    // jsdom 会把 oklch 的数值归一化（0.30 → 0.3），断言按归一化后的形式写。
    expect(row.style.background).toContain("oklch(0.3 0.1 25");
  });

  it("ignores warnings on a failed task and keeps showing the error", async () => {
    const user = userEvent.setup();
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "failed-warn",
          status: "failed",
          media_type: "video",
          resource_id: "FAILEDWARN",
          error_message: "provider rejected the request",
          result: { warnings: [WARNING_TEXT] },
        }),
      ],
    });
    render(<HostedTaskHud />);

    await user.click(screen.getByText("FAILEDWARN").closest('[role="button"]') as HTMLElement);

    expect(screen.getByText("provider rejected the request")).toBeTruthy();
    expect(screen.queryByText(WARNING_TEXT)).toBeNull();
  });
});
