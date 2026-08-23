import { render, screen, cleanup, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement, useRef } from "react";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import i18n from "@/i18n";

// framer-motion 的退场动画在 jsdom 里不会走完，被过滤掉的行会滞留在 DOM 中。
// 本文件断言的是过滤后的可见集合，与动画无关，故把 motion 元素替换成裸 DOM、
// AnimatePresence 替换成透传容器，让卸载即时生效。
vi.mock("framer-motion", () => {
  const motion = new Proxy(
    {},
    {
      get: (_target, tag: string) =>
        function MotionStub({
          children,
          layout: _layout,
          initial: _initial,
          animate: _animate,
          exit: _exit,
          transition: _transition,
          ...rest
        }: Record<string, unknown> & { children?: React.ReactNode }) {
          return createElement(tag, rest, children);
        },
    },
  );
  return {
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => children,
  };
});

// 终态任务不再自动淡出，改由顶部状态过滤 pill 控制可见集合。

const emptyStats = {
  queued: 0,
  running: 0,
  cancelling: 0,
  succeeded: 0,
  failed: 0,
  cancelled: 0,
  total: 0,
};

// 任务行本身也是 role="button"（展开详情），且行内含"失败"等状态文案，
// 全局按名查按钮会与过滤 pill 撞名，故所有 pill 查询都限定在过滤分组内。
function pill(name: RegExp) {
  return within(screen.getByRole("group", { name: "按状态筛选任务" })).getByRole("button", {
    name,
  });
}

function HostedTaskHud() {
  const anchorRef = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={anchorRef} data-testid="anchor" />
      <TaskHud anchorRef={anchorRef} />
    </div>
  );
}

const TASKS = [
  makeTask({
    task_id: "run-1",
    status: "running",
    task_type: "video",
    media_type: "video",
    resource_id: "RUNNING1",
  }),
  makeTask({
    task_id: "queued-1",
    status: "queued",
    task_type: "video",
    media_type: "video",
    resource_id: "QUEUED1",
  }),
  makeTask({
    task_id: "failed-1",
    status: "failed",
    task_type: "video",
    media_type: "video",
    resource_id: "FAILED1",
    error_message: "boom",
  }),
  makeTask({
    task_id: "done-1",
    status: "succeeded",
    task_type: "video",
    media_type: "video",
    resource_id: "DONE1",
  }),
  makeTask({
    task_id: "cancelled-1",
    status: "cancelled",
    task_type: "video",
    media_type: "video",
    resource_id: "CANCELLED1",
  }),
];

describe("TaskHud status filter", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh");
    useAppStore.setState({ taskHudOpen: true });
    useTasksStore.setState({ tasks: TASKS, stats: emptyStats });
  });

  afterEach(() => {
    cleanup();
    useAppStore.setState({ taskHudOpen: false });
    useTasksStore.setState({ tasks: [], stats: emptyStats });
  });

  it("shows every task under the default 'all' filter", async () => {
    render(<HostedTaskHud />);

    for (const resourceId of ["RUNNING1", "QUEUED1", "FAILED1", "DONE1", "CANCELLED1"]) {
      expect(await screen.findByText(resourceId)).toBeTruthy();
    }
  });

  it("keeps terminal tasks visible instead of fading them out", async () => {
    vi.useFakeTimers();
    try {
      render(<HostedTaskHud />);
      expect(screen.getByText("DONE1")).toBeTruthy();

      // 旧实现在 3s 后淡出、3.4s 后移除终态任务。
      await act(async () => {
        vi.advanceTimersByTime(10_000);
      });

      expect(screen.getByText("DONE1")).toBeTruthy();
      expect(screen.getByText("CANCELLED1")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("narrows the list to running/cancelling/queued under 'active'", async () => {
    const user = userEvent.setup();
    render(<HostedTaskHud />);

    await user.click(pill(/进行中/));

    expect(screen.getByText("RUNNING1")).toBeTruthy();
    expect(screen.getByText("QUEUED1")).toBeTruthy();
    expect(screen.queryByText("FAILED1")).toBeNull();
    expect(screen.queryByText("DONE1")).toBeNull();
  });

  it("narrows the list to failed tasks under 'failed'", async () => {
    const user = userEvent.setup();
    render(<HostedTaskHud />);

    await user.click(pill(/失败/));

    expect(screen.getByText("FAILED1")).toBeTruthy();
    expect(screen.queryByText("RUNNING1")).toBeNull();
    expect(screen.queryByText("DONE1")).toBeNull();
  });

  it("groups succeeded and cancelled under 'done'", async () => {
    const user = userEvent.setup();
    render(<HostedTaskHud />);

    await user.click(pill(/完成/));

    expect(screen.getByText("DONE1")).toBeTruthy();
    expect(screen.getByText("CANCELLED1")).toBeTruthy();
    expect(screen.queryByText("FAILED1")).toBeNull();
  });

  it("marks the selected pill as pressed", async () => {
    const user = userEvent.setup();
    render(<HostedTaskHud />);

    expect(pill(/全部/).getAttribute("aria-pressed")).toBe("true");

    await user.click(pill(/失败/));

    expect(pill(/失败/).getAttribute("aria-pressed")).toBe("true");
    expect(pill(/全部/).getAttribute("aria-pressed")).toBe("false");
  });

  it("counts the loaded tasks per bucket", async () => {
    render(<HostedTaskHud />);

    expect(pill(/全部/).textContent).toContain("5");
    expect(pill(/进行中/).textContent).toContain("2");
    expect(pill(/失败/).textContent).toContain("1");
    expect(pill(/完成/).textContent).toContain("2");
  });

  it("keeps running tasks above queued and terminal ones", async () => {
    render(<HostedTaskHud />);

    const rendered = ["RUNNING1", "QUEUED1", "FAILED1", "DONE1", "CANCELLED1"].map(
      (resourceId) => screen.getByText(resourceId),
    );
    const positions = rendered.map((node) => {
      const all = Array.from(document.querySelectorAll("span"));
      return all.indexOf(node as HTMLSpanElement);
    });
    expect(positions[0]).toBeLessThan(positions[1]);
    expect(positions[1]).toBeLessThan(positions[2]);
  });

  it("shows a filter-specific empty message when nothing matches", async () => {
    const user = userEvent.setup();
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "only-running",
          status: "running",
          media_type: "video",
          resource_id: "ONLY1",
        }),
      ],
    });
    render(<HostedTaskHud />);

    await user.click(pill(/失败/));

    expect(screen.getAllByText("当前筛选下暂无任务").length).toBeGreaterThan(0);
  });

  it("does not cap terminal tasks at five per channel", async () => {
    const many = Array.from({ length: 8 }, (_, index) =>
      makeTask({
        task_id: `done-${index}`,
        status: "succeeded",
        media_type: "video",
        resource_id: `MANY${index}`,
      }),
    );
    useTasksStore.setState({ tasks: many });
    render(<HostedTaskHud />);

    for (let index = 0; index < 8; index += 1) {
      expect(screen.getByText(`MANY${index}`)).toBeTruthy();
    }
  });
});
