import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { FinalCutStatus, JianyingDraftStatus, TaskItem } from "@/types";
import { RenderButton } from "./RenderButton";
import { RenderDialog } from "./RenderDialog";

function finalCut(overrides: Partial<FinalCutStatus> = {}): FinalCutStatus {
  return {
    episode: 1,
    timeline_id: "tl-1",
    narration: "without_narration",
    subtitles: "burned",
    status: "missing",
    artifact_path: "renders/episode_1/tl-1/final_cut.mp4",
    version: null,
    rendered_at: null,
    download_url: null,
    ...overrides,
  };
}

function draft(overrides: Partial<JianyingDraftStatus> = {}): JianyingDraftStatus {
  return {
    episode: 1,
    timeline_id: "tl-1",
    narration: "without_narration",
    status: "missing",
    artifact_path: "renders/episode_1/tl-1/jianying_draft.without_narration.zip",
    version: null,
    rendered_at: null,
    ...overrides,
  };
}

function task(status: TaskItem["status"], overrides: Partial<TaskItem> = {}): TaskItem {
  return { task_id: "task-1", task_type: "render_final_cut", status, error_message: null, ...overrides } as TaskItem;
}

function renderDialog(blockedReason: string | null = null) {
  return render(
    <RenderDialog
      open
      onClose={vi.fn()}
      projectName="demo"
      timelineId="tl-1"
      timelineName="初剪"
      blockedReason={blockedReason}
    />,
  );
}

describe("RenderDialog", () => {
  let anchorClick: ReturnType<typeof vi.spyOn>;
  let downloads: { href: string; download: string }[];

  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ toast: null });
    vi.spyOn(API, "listTasks").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    downloads = [];
    anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push({ href: this.getAttribute("href") ?? "", download: this.download });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("标题带剪辑时间线名称，已是最新的成片直接下载、不重新渲染", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(
      finalCut({ status: "current", version: 3, download_url: "/api/v1/files/demo/renders/final_cut.mp4?v=3" }),
    );
    const renderSpy = vi.spyOn(API, "renderFinalCut");

    renderDialog();

    expect(screen.getByRole("dialog", { name: "出片 · 初剪" })).toBeInTheDocument();
    expect(await screen.findByText("已是最新")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下载" }));

    expect(downloads).toEqual([{ href: "/api/v1/files/demo/renders/final_cut.mp4?v=3", download: "初剪.mp4" }]);
    expect(renderSpy).not.toHaveBeenCalled();
  });

  it("过时的成片仍可下载，并以重新渲染为主动作", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(
      finalCut({ status: "stale", version: 2, download_url: "/api/v1/files/demo/x.mp4?v=2" }),
    );

    renderDialog();

    expect(await screen.findByText("已落后于剪辑时间线")).toBeInTheDocument();
    expect(screen.getByText("旧文件仍可下载，重新生成后会被替换。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "重新渲染" })).toBeEnabled();
  });

  it("提交后在对话框内显示任务进度", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    const submit = vi
      .spyOn(API, "renderFinalCut")
      .mockResolvedValue({ task_id: "task-1", deduped: false, artifact_path: "renders/x.mp4" });
    vi.spyOn(API, "getTask").mockResolvedValue(task("running"));

    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "渲染成片" }));

    expect(await screen.findByText("正在渲染成片…")).toBeInTheDocument();
    expect(submit).toHaveBeenCalledWith("demo", "tl-1");
    expect(API.getTask).toHaveBeenCalledWith("task-1");
    expect(screen.getByRole("button", { name: "渲染成片" })).toBeDisabled();
  });

  it("重新打开时接回同一产物的在途任务，不重复提交", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    const submit = vi.spyOn(API, "renderFinalCut");
    vi.spyOn(API, "getTask").mockResolvedValue(task("running", { task_id: "task-9" }));
    const payload = { timeline_id: "tl-1", narration: "without_narration", subtitles: "burned" };
    vi.mocked(API.listTasks).mockImplementation(async ({ status } = {}) => {
      const items =
        status === "running"
          ? [task("running", { task_id: "task-other", payload: { ...payload, timeline_id: "tl-2" } })]
          : [task("queued", { task_id: "task-9", payload })];
      return { items, total: items.length, page: 1, page_size: 50 };
    });

    renderDialog();

    expect(await screen.findByText("正在渲染成片…")).toBeInTheDocument();
    expect(API.listTasks).toHaveBeenCalledWith({ projectName: "demo", taskType: "render_final_cut", status: "queued" });
    expect(API.getTask).toHaveBeenCalledWith("task-9");
    expect(API.getTask).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "渲染成片" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

    it("任务完成后刷新产物现状并可以下载", async () => {
    const status = vi
      .spyOn(API, "getFinalCutStatus")
      .mockResolvedValueOnce(finalCut())
      .mockResolvedValueOnce(finalCut({ status: "current", version: 1, download_url: "/api/v1/files/demo/a.mp4?v=1" }));
    vi.spyOn(API, "renderFinalCut").mockResolvedValue({ task_id: "task-1", deduped: false, artifact_path: "a" });
    vi.spyOn(API, "getTask").mockResolvedValue(task("succeeded"));

    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "渲染成片" }));

    expect(await screen.findByText("已完成，可以下载")).toBeInTheDocument();
    expect(await screen.findByText("已是最新")).toBeInTheDocument();
    expect(status).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "下载" })).toBeEnabled();
  });

  it("任务失败时显示失败原因", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    vi.spyOn(API, "renderFinalCut").mockResolvedValue({ task_id: "task-1", deduped: false, artifact_path: "a" });
    vi.spyOn(API, "getTask").mockResolvedValue(task("failed", { error_message: "成片验收未通过" }));

    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "渲染成片" }));

    expect(await screen.findByText("出片失败：成片验收未通过")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "渲染成片" })).toBeEnabled();
  });

  it("提交被入队前检查拒绝时显示原因", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    vi.spyOn(API, "renderFinalCut").mockRejectedValue(new Error("E1S03 还没有可用视频"));

    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "渲染成片" }));

    expect(await screen.findByText("无法提交：E1S03 还没有可用视频")).toBeInTheDocument();
  });

  it("剪映草稿按本机目录与剪映版本下载，并记住目录", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    vi.spyOn(API, "getJianyingDraftStatus").mockResolvedValue(draft({ status: "current", version: 4 }));
    const token = vi
      .spyOn(API, "requestExportToken")
      .mockResolvedValue({ download_token: "tok", expires_in: 300, diagnostics: { blocking: [], auto_fixed: [], warnings: [] } });

    renderDialog();
    await userEvent.click(screen.getByRole("radio", { name: /剪映草稿/ }));
    expect(await screen.findByText("已是最新")).toBeInTheDocument();

    const download = screen.getByRole("button", { name: "下载" });
    expect(download).toBeDisabled();
    await userEvent.type(screen.getByLabelText("草稿目录路径"), "/Users/me/Drafts");
    await userEvent.selectOptions(screen.getByLabelText("剪映版本"), "5");
    await userEvent.click(download);

    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(token).toHaveBeenCalledWith("demo", "current");
    expect(downloads[0].href).toBe(
      "/api/v1/projects/demo/edit-timelines/tl-1/jianying-draft/download" +
        "?draft_path=%2FUsers%2Fme%2FDrafts&download_token=tok&jianying_version=5",
    );
    expect(localStorage.getItem("arcreel_jianying_draft_path")).toBe("/Users/me/Drafts");
  });

  it("切换交付物时保留已填写的草稿目录与剪映版本", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    vi.spyOn(API, "getJianyingDraftStatus").mockResolvedValue(draft());

    renderDialog();
    await userEvent.click(screen.getByRole("radio", { name: /剪映草稿/ }));
    await userEvent.type(screen.getByLabelText("草稿目录路径"), "/Users/me/Drafts");
    await userEvent.selectOptions(screen.getByLabelText("剪映版本"), "5");
    await userEvent.click(screen.getByRole("radio", { name: /成片/ }));
    await userEvent.click(screen.getByRole("radio", { name: /剪映草稿/ }));

    expect(screen.getByLabelText("草稿目录路径")).toHaveValue("/Users/me/Drafts");
    expect(screen.getByLabelText("剪映版本")).toHaveValue("5");
  });

  it("剪映草稿走自己的提交端点", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    vi.spyOn(API, "getJianyingDraftStatus").mockResolvedValue(draft());
    const exportDraft = vi
      .spyOn(API, "exportJianyingDraft")
      .mockResolvedValue({ task_id: "task-2", deduped: false, artifact_path: "b" });
    vi.spyOn(API, "getTask").mockResolvedValue(task("running", { task_id: "task-2" }));

    renderDialog();
    await userEvent.click(screen.getByRole("radio", { name: /剪映草稿/ }));
    await userEvent.click(await screen.findByRole("button", { name: "导出剪映草稿" }));

    expect(await screen.findByText("正在导出剪映草稿…")).toBeInTheDocument();
    expect(exportDraft).toHaveBeenCalledWith("demo", "tl-1");
  });

  it("对话框打开期间出现阻断级 issue 时不能出片", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());

    renderDialog("当前剪辑时间线有 1 个问题阻断出片，处理后才能出片");

    expect(await screen.findByRole("button", { name: "渲染成片" })).toBeDisabled();
    expect(screen.getByText("当前剪辑时间线有 1 个问题阻断出片，处理后才能出片")).toBeInTheDocument();
    expect(anchorClick).not.toHaveBeenCalled();
  });
});

describe("RenderButton", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("有阻断级 issue 时不可点，悬停说明原因并链接到问题列表", async () => {
    const onShowIssues = vi.fn();
    render(
      <RenderButton
        projectName="demo"
        timelineId="tl-1"
        timelineName="初剪"
        issues={[
          { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S03" },
          { code: "trim_ignored", severity: "info", applies_to: "all", unit_id: "E1S01" },
        ]}
        onShowIssues={onShowIssues}
      />,
    );

    const button = screen.getByRole("button", { name: "出片" });
    const reason = "当前剪辑时间线有 1 个问题阻断出片，涉及 E1S03，处理后才能出片";
    expect(button).toBeDisabled();
    expect(button.parentElement).toHaveAttribute("title", reason);
    expect(button).toHaveAccessibleDescription(reason);
    await userEvent.click(screen.getByRole("button", { name: "查看问题（1）" }));
    expect(onShowIssues).toHaveBeenCalled();
  });

  it("没有阻断级 issue 时打开当前剪辑时间线的出片对话框", async () => {
    vi.spyOn(API, "getFinalCutStatus").mockResolvedValue(finalCut());
    vi.spyOn(API, "listTasks").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    render(
      <RenderButton
        projectName="demo"
        timelineId="tl-1"
        timelineName="初剪"
        issues={[{ code: "hold_too_long", severity: "warning", applies_to: "all", unit_id: "E1S02" }]}
        onShowIssues={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "出片" }));

    const dialog = await screen.findByRole("dialog", { name: "出片 · 初剪" });
    expect(within(dialog).queryByRole("combobox", { name: /剪辑时间线/ })).not.toBeInTheDocument();
    expect(API.getFinalCutStatus).toHaveBeenCalledWith("demo", "tl-1", expect.anything());
  });
});
