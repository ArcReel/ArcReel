import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useRef } from "react";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import i18n from "@/i18n";

// 后端把上游确定性 4xx 编成 `provider_rejected`：error_message 是本地化文案，
// 拒因摘要作为 error_params.provider_reason 的上游原文单独回传，不参与翻译。

const emptyStats = {
  queued: 0,
  running: 0,
  cancelling: 0,
  succeeded: 0,
  failed: 0,
  cancelled: 0,
  total: 0,
};

const REASON = "DataInspectionFailed: input data may contain inappropriate content";

function HostedTaskHud() {
  const anchorRef = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={anchorRef} data-testid="anchor" />
      <TaskHud anchorRef={anchorRef} />
    </div>
  );
}

function rejectedTask(errorParams: Record<string, unknown>) {
  return makeTask({
    task_id: "rejected-1",
    status: "failed",
    task_type: "video",
    media_type: "video",
    resource_id: "REJECTED1",
    error_message: "供应商拒绝了这次生成请求（HTTP 400）",
    error_code: "provider_rejected",
    error_params: errorParams,
  });
}

function expandRow() {
  render(<HostedTaskHud />);
  fireEvent.click(screen.getByText("REJECTED1").closest('[role="button"]') as HTMLElement);
}

describe("TaskHud provider rejection reason", () => {
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

  it("shows the upstream reason verbatim next to the localized failure text", () => {
    useTasksStore.setState({ tasks: [rejectedTask({ status: 400, provider_reason: REASON })] });

    expandRow();

    expect(screen.getByText("供应商拒绝了这次生成请求（HTTP 400）", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(REASON)).toBeInTheDocument();
    expect(screen.getByText("供应商拒因")).toBeInTheDocument();
  });

  it("omits the reason block when the failure carries no summary", () => {
    useTasksStore.setState({ tasks: [rejectedTask({ status: 400 })] });

    expandRow();

    expect(screen.queryByText("供应商拒因")).not.toBeInTheDocument();
  });

  it("leaves other failure codes rendering the message alone", () => {
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "download-1",
          status: "failed",
          media_type: "video",
          resource_id: "DOWNLOAD1",
          error_message: "视频生成任务已成功但下载失败",
          error_code: "artifact_download_failed",
          error_params: { provider_reason: REASON },
        }),
      ],
    });
    render(<HostedTaskHud />);

    fireEvent.click(screen.getByText("DOWNLOAD1").closest('[role="button"]') as HTMLElement);

    expect(screen.queryByText(REASON)).not.toBeInTheDocument();
  });
});
