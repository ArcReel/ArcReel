import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@/i18n";
import { makeTask } from "@/test/factories";
import { TaskElapsedReadout } from "./TaskElapsedReadout";

const START = Date.parse("2026-04-20T00:00:00Z");

describe("TaskElapsedReadout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(START);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("执行中任务的读数随时间自行递增，无需重新渲染", () => {
    const task = makeTask({ status: "running", started_at: "2026-04-20T00:00:00Z" });
    render(<TaskElapsedReadout task={task} />);
    expect(screen.getByText("0秒")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText("3秒")).toBeInTheDocument();
  });

  it("排队任务展示已等待时长", () => {
    vi.setSystemTime(START + 90_000);
    const task = makeTask({ status: "queued", queued_at: "2026-04-20T00:00:00Z" });
    render(<TaskElapsedReadout task={task} />);
    expect(screen.getByText("1分30秒")).toBeInTheDocument();
    expect(screen.getByTitle("已等待 1分30秒")).toBeInTheDocument();
  });

  it("终态任务展示总耗时且不再递增", () => {
    const task = makeTask({
      status: "succeeded",
      started_at: "2026-04-20T00:00:00Z",
      finished_at: "2026-04-20T00:00:20Z",
    });
    render(<TaskElapsedReadout task={task} />);
    expect(screen.getByTitle("耗时 20秒")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(screen.getByText("20秒")).toBeInTheDocument();
  });

  it("started_at 缺失的执行中任务不渲染任何读数", () => {
    const { container } = render(
      <TaskElapsedReadout task={makeTask({ status: "running", started_at: null })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("终态但时间戳缺失时不渲染读数，也不起计时器", () => {
    const setInterval = vi.spyOn(globalThis, "setInterval");
    const { container } = render(
      <TaskElapsedReadout
        task={makeTask({ status: "failed", started_at: "2026-04-20T00:00:00Z", finished_at: null })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(setInterval).not.toHaveBeenCalled();
  });

  it("多个执行中任务共用一个计时器", () => {
    const setInterval = vi.spyOn(globalThis, "setInterval");
    const task = makeTask({ status: "running", started_at: "2026-04-20T00:00:00Z" });
    render(
      <>
        <TaskElapsedReadout task={task} />
        <TaskElapsedReadout task={makeTask({ ...task, task_id: "t2" })} />
        <TaskElapsedReadout task={makeTask({ ...task, task_id: "t3" })} />
      </>,
    );
    expect(setInterval).toHaveBeenCalledTimes(1);
  });
});
