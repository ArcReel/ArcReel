import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import type { ImportFailureDiagnostics, ProjectSummary } from "@/types";
import type { WorkflowMigrationReport } from "@/types/workflow";

const UPGRADE_REPORT: WorkflowMigrationReport = {
  schema_version: 2,
  migrated_at: "2026-10-13T00:00:00Z",
  from_schema_version: 14,
  to_schema_version: 18,
  complete: true,
  registered: {},
  adjusted: [
    {
      code: "script_binding_canonicalized",
      episode: 3,
      file: "scripts/episode_3.json",
      backup: null,
      params: { from: "scripts/custom.json", to: "scripts/episode_3.json" },
    },
  ],
  skipped: [],
  acknowledged_at: null,
};

vi.mock("@/components/pages/CreateProjectModal", () => ({
  CreateProjectModal: () => <div data-testid="create-project-modal">Create Project Modal</div>,
}));

const DAY_MS = 24 * 60 * 60 * 1000;

interface SummaryOptions {
  title?: string;
  episodes?: { total: number; completed?: number; inProduction?: number };
  needsRepair?: boolean;
  repairReason?: string;
  sourceRemaining?: boolean;
  lastActivityAt?: string | null;
}

function summary(name: string, options: SummaryOptions = {}): ProjectSummary {
  const { total = 0, completed = 0, inProduction = 0 } = options.episodes ?? { total: 0 };
  return {
    name,
    title: options.title ?? name,
    style: "Anime",
    thumbnail: null,
    last_activity_at: options.lastActivityAt ?? null,
    status: {
      needs_repair: options.needsRepair ?? false,
      repair_reason: options.repairReason ?? null,
      assets: {
        character: { total: 0, available: 0, stale: 0 },
        scene: { total: 0, available: 0, stale: 0 },
        prop: { total: 0, available: 0, stale: 0 },
      },
      episodes_summary: { total, scripted: total, in_production: inProduction, completed },
      source_remaining: options.sourceRemaining ?? false,
    },
  };
}

function importResult(projectName: string, diagnostics: { auto_fixed: never[] | { code: string; message: string }[]; warnings: { code: string; message: string }[] }) {
  return {
    success: true,
    project_name: projectName,
    project: {
      title: "Imported Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: {},
      scenes: {},
      props: {},
    },
    warnings: [],
    conflict_resolution: "none",
    diagnostics,
  } as Awaited<ReturnType<typeof API.importProject>>;
}

function importError(
  message: string,
  extra: {
    status?: number;
    conflict_project_name?: string;
    confirmation_required?: boolean;
    migration_report?: WorkflowMigrationReport;
    migration_report_episodes?: { episode: number; title: string }[];
    diagnostics?: ImportFailureDiagnostics;
  } = {},
) {
  return Object.assign(new Error(message), extra);
}

function renderPage() {
  const location = memoryLocation({ path: "/app/projects", record: true });
  return {
    ...render(
      <Router hook={location.hook}>
        <ProjectsPage />
      </Router>,
    ),
    location,
  };
}

function chooseZip(container: HTMLElement, name = "project.zip") {
  const file = new File(["zip"], name, { type: "application/zip" });
  fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
  return file;
}

async function projectList() {
  return screen.findByRole("list", { name: "项目" });
}

async function openCardMenu(title: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: `「${title}」的更多操作` }));
  return user;
}

describe("ProjectsPage", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows a loading status while projects are being fetched", () => {
    vi.spyOn(API, "listProjects").mockImplementation(() => new Promise(() => {}));

    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("加载项目列表…");
  });

  it("explains a failed load instead of showing an empty lobby", async () => {
    vi.spyOn(API, "listProjects").mockRejectedValue(new Error("network down"));

    renderPage();
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toBe("项目列表加载失败：network down");
    });
  });

  it("invites creating or importing the first project when there are none", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });

    renderPage();

    expect(await screen.findByText("还没有项目")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/从第一部作品开始吧。/);
    expect(screen.getByRole("button", { name: "导入 ZIP" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "新建项目" })).toHaveLength(2);
    expect(screen.queryByRole("list", { name: "项目" })).not.toBeInTheDocument();
  });

  it("opens the create wizard from the header's primary button", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("demo")] });

    renderPage();
    await projectList();
    // 已有项目时网格里不放「新建项目」卡，入口只在顶栏
    const [create] = screen.getAllByRole("button", { name: "新建项目" });
    expect(screen.getAllByRole("button", { name: "新建项目" })).toHaveLength(1);
    fireEvent.click(create);

    expect(await screen.findByTestId("create-project-modal")).toBeInTheDocument();
  });

  it("opens the zip picker from the create button's dropdown", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("demo")] });
    const pick = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});

    renderPage();
    await projectList();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "更多新建方式" }));
    await user.click(await screen.findByRole("menuitem", { name: "导入 ZIP…" }));

    expect(pick).toHaveBeenCalled();
  });

  it("links to external agent access from the header", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    renderPage();

    expect(await screen.findByRole("link", { name: "外部 Agent 接入" })).toHaveAttribute(
      "href",
      "/app/settings?section=external-agent",
    );
  });

  it("does not mark settings incomplete when only the embedded-agent credential is missing", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "getProviders").mockResolvedValue({
      providers: [{
        id: "gemini",
        display_name: "Google Gemini",
        description: "Google Gemini API",
        status: "ready",
        media_types: ["image", "video", "text"],
        capabilities: [],
        credential_count: 1,
        models: {},
      }],
    });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue({
      settings: { anthropic_api_key: { is_set: false, masked: null } },
    } as never);

    await useConfigStatusStore.getState().fetch();
    expect(useConfigStatusStore.getState().isComplete).toBe(true);

    renderPage();

    await screen.findByText("还没有项目");
    expect(screen.queryByLabelText("配置不完整")).not.toBeInTheDocument();
  });

  it("reports each card's episode progress and when it was last worked on", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        summary("halfway", {
          title: "Halfway",
          episodes: { total: 4, completed: 1, inProduction: 1 },
          lastActivityAt: new Date(Date.now() - 3 * DAY_MS).toISOString(),
        }),
        summary("done", { title: "Done", episodes: { total: 3, completed: 3 } }),
        summary("fresh", { title: "Fresh", lastActivityAt: new Date().toISOString() }),
      ],
    });

    renderPage();
    const list = await projectList();

    const halfway = within(list).getByRole("link", { name: /Halfway/ });
    expect(halfway).toHaveAttribute("href", "/app/projects/halfway");
    expect(halfway).toHaveTextContent("制作中");
    expect(halfway).toHaveTextContent("已完成 1 / 4 集 · 3天前更新");

    const done = within(list).getByRole("link", { name: /Done/ });
    expect(done).toHaveTextContent("已完成");
    expect(done).toHaveTextContent(/^.*3 集$/);

    const fresh = within(list).getByRole("link", { name: /Fresh/ });
    expect(fresh).toHaveTextContent("尚未建集");
    expect(fresh).toHaveTextContent("还没有集 · 刚刚更新");
  });

  it("flags a project that needs repair and says why", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        summary("broken", {
          title: "Broken",
          episodes: { total: 2 },
          needsRepair: true,
          repairReason: "迁移步 v7 失败",
        }),
      ],
    });

    renderPage();
    const card = within(await projectList()).getByRole("link", { name: /Broken/ });

    expect(card).toHaveTextContent("待修复");
    expect(card).toHaveTextContent("迁移步 v7 失败");
  });

  it("counts projects per filter and narrows the grid to the chosen one", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        summary("empty"),
        summary("halfway", { episodes: { total: 4, completed: 2 } }),
        summary("broken", { episodes: { total: 2 }, needsRepair: true, repairReason: "broken" }),
        summary("done", { episodes: { total: 3, completed: 3 } }),
        // 已切出的集全部完成，但源文还没规划完：项目顶栏提示继续分集规划，大厅也不算完成
        summary("unplanned", { episodes: { total: 10, completed: 10 }, sourceRemaining: true }),
      ],
    });

    renderPage();
    await projectList();

    // 没有集的项目算进行中；「待修复」与进度正交，待修复的项目同时计入两种筛选。
    expect(screen.getByRole("button", { name: /^全部\s*5$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^进行中\s*4$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^待修复\s*1$/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^已完成\s*1$/ }));
    const shown = within(await projectList()).getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(shown).toEqual(["/app/projects/done"]);
  });

  it("keeps the server's most-recent-first order", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [summary("newest"), summary("older"), summary("oldest")],
    });

    renderPage();
    const hrefs = within(await projectList()).getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(hrefs).toEqual(["/app/projects/newest", "/app/projects/older", "/app/projects/oldest"]);
  });

  it("searches titles and project IDs, and clears back to every project", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [summary("night-rain", { title: "夜雨" }), summary("harbor", { title: "港口" })],
    });

    renderPage();
    await projectList();
    const search = screen.getByRole("searchbox", { name: "搜索项目" });

    fireEvent.change(search, { target: { value: "harbor" } });
    expect(within(await projectList()).getAllByRole("link")).toHaveLength(1);

    fireEvent.change(search, { target: { value: "不存在" } });
    expect(await screen.findByText("没有匹配的项目")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(within(await projectList()).getAllByRole("link")).toHaveLength(2);
  });

  it("renames a project without changing its ID", async () => {
    vi.spyOn(API, "listProjects")
      .mockResolvedValueOnce({ projects: [summary("second", { title: "Second" })] })
      .mockResolvedValueOnce({ projects: [summary("second", { title: "Second Cut" })] });
    vi.spyOn(API, "updateProject").mockResolvedValue({ success: true } as never);

    renderPage();
    const user = await openCardMenu("Second");
    await user.click(await screen.findByRole("menuitem", { name: "重命名" }));
    const dialog = await screen.findByRole("dialog", { name: "重命名项目" });
    expect(dialog).toHaveTextContent("项目 ID「second」不随标题变化");

    const input = within(dialog).getByRole("textbox", { name: "项目标题" });
    await user.clear(input);
    await user.type(input, "  Second Cut  ");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(API.updateProject).toHaveBeenCalledWith("second", { title: "Second Cut" });
    const card = await within(await projectList()).findByRole("link", { name: /Second Cut/ });
    expect(card).toHaveAttribute("href", "/app/projects/second");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "重命名项目" })).not.toBeInTheDocument());
  });

  it("keeps the rename dialog open and shows why saving failed", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("second", { title: "Second" })] });
    vi.spyOn(API, "updateProject").mockRejectedValue(new Error("标题不能超过 100 字"));

    renderPage();
    const user = await openCardMenu("Second");
    await user.click(await screen.findByRole("menuitem", { name: "重命名" }));
    const dialog = await screen.findByRole("dialog", { name: "重命名项目" });
    await user.type(within(dialog).getByRole("textbox", { name: "项目标题" }), "!");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("标题不能超过 100 字");
  });

  it("deletes a project only after confirming, then drops it from the grid", async () => {
    vi.spyOn(API, "listProjects")
      .mockResolvedValueOnce({ projects: [summary("first", { title: "First" }), summary("second", { title: "Second" })] })
      .mockResolvedValueOnce({ projects: [summary("first", { title: "First" })] });
    vi.spyOn(API, "deleteProject").mockResolvedValue({ success: true } as never);

    renderPage();
    const user = await openCardMenu("Second");
    await user.click(await screen.findByRole("menuitem", { name: "删除" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除「Second」？" });
    expect(API.deleteProject).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "删除项目" }));

    expect(API.deleteProject).toHaveBeenCalledWith("second");
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: /Second/ })).not.toBeInTheDocument();
    });
  });

  it("keeps the delete confirmation open and shows the backend's reason when deleting fails", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("second", { title: "Second" })] });
    vi.spyOn(API, "deleteProject").mockRejectedValue(new Error("项目名称 'second' 非法"));

    renderPage();
    const user = await openCardMenu("Second");
    await user.click(await screen.findByRole("menuitem", { name: "删除" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除「Second」？" });
    await user.click(within(dialog).getByRole("button", { name: "删除项目" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("项目名称 'second' 非法");
  });

  const deletedDemo = {
    id: "demo#deleted-20261020T080000Z",
    name: "demo",
    title: "Old",
    deleted_at: "2026-10-20T08:00:00+00:00",
    expires_at: "2026-11-19T08:00:00+00:00",
    size: 3 * 1024 * 1024,
  };

  it("shows how much space each deleted project takes", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "listDeletedProjects").mockResolvedValue({ projects: [deletedDemo], retention_days: 30 });

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "已删除项目" }));
    const dialog = await screen.findByRole("dialog", { name: "已删除项目" });

    expect(await within(dialog).findByText(/占用 3\.0 MB$/)).toBeInTheDocument();
  });

  it("reloads deleted projects after a restore cancels the reopened dialog's in-flight listing", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    const deletedMeanwhile = { ...deletedDemo, id: "other#deleted-20261021T080000Z", name: "other", title: "Other" };
    vi.spyOn(API, "listDeletedProjects")
      .mockResolvedValueOnce({ projects: [deletedDemo], retention_days: 30 })
      .mockReturnValueOnce(new Promise(() => {}))
      .mockResolvedValue({ projects: [deletedMeanwhile], retention_days: 30 });
    vi.spyOn(API, "restoreDeletedProject").mockResolvedValue({ name: "demo" });

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "已删除项目" }));
    const first = await screen.findByRole("dialog", { name: "已删除项目" });
    await within(first).findByRole("button", { name: "恢复「Old」" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "已删除项目" })).not.toBeInTheDocument());

    // 重开时先显示缓存的列表，新的列表请求还在途；恢复缓存里的项目会作废它，之后要补拉
    await user.click(screen.getByRole("button", { name: "已删除项目" }));
    const dialog = await screen.findByRole("dialog", { name: "已删除项目" });
    await user.click(await within(dialog).findByRole("button", { name: "恢复「Old」" }));

    expect(await within(dialog).findByRole("button", { name: "恢复「Other」" })).toBeInTheDocument();
  });

  it("restores a deleted project under a new name when the original is taken, and reloads the grid", async () => {
    vi.spyOn(API, "listProjects")
      .mockResolvedValueOnce({ projects: [summary("demo", { title: "New" })] })
      .mockResolvedValueOnce({
        projects: [summary("demo", { title: "New" }), summary("demo-2", { title: "Old" })],
      });
    vi.spyOn(API, "listDeletedProjects")
      .mockResolvedValueOnce({ projects: [deletedDemo], retention_days: 30 })
      .mockResolvedValue({ projects: [], retention_days: 30 });
    vi.spyOn(API, "restoreDeletedProject").mockResolvedValue({ name: "demo-2" });

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "已删除项目" }));
    const dialog = await screen.findByRole("dialog", { name: "已删除项目" });
    await user.click(await within(dialog).findByRole("button", { name: "恢复「Old」" }));

    expect(API.restoreDeletedProject).toHaveBeenCalledWith("demo#deleted-20261020T080000Z");
    expect(await within(dialog).findByText("没有已删除项目")).toBeInTheDocument();
    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toBe("原名已被占用，「Old」已恢复为 demo-2");
    });
    // 对话框是模态的，背后的网格对无障碍树隐藏：按重新加载的请求判断网格已刷新
    await waitFor(() => expect(API.listProjects).toHaveBeenCalledTimes(2));
  });

  it("does not report a restore as successful when the project list fails to reload", async () => {
    vi.spyOn(API, "listProjects")
      .mockResolvedValueOnce({ projects: [] })
      .mockRejectedValueOnce(new Error("network down"));
    vi.spyOn(API, "listDeletedProjects")
      .mockResolvedValueOnce({ projects: [deletedDemo], retention_days: 30 })
      .mockResolvedValue({ projects: [], retention_days: 30 });
    vi.spyOn(API, "restoreDeletedProject").mockResolvedValue({ name: "demo" });
    const toasts: string[] = [];
    const unsubscribe = useAppStore.subscribe((state, previous) => {
      if (state.toast && state.toast !== previous.toast) toasts.push(state.toast.text);
    });

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "已删除项目" }));
    const dialog = await screen.findByRole("dialog", { name: "已删除项目" });
    await user.click(await within(dialog).findByRole("button", { name: "恢复「Old」" }));

    await waitFor(() => expect(toasts).toEqual(["项目列表加载失败：network down"]));
    unsubscribe();
  });

  it("purges a deleted project only after a second confirmation", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "listDeletedProjects")
      .mockResolvedValueOnce({ projects: [deletedDemo], retention_days: 30 })
      .mockResolvedValue({ projects: [], retention_days: 30 });
    vi.spyOn(API, "purgeDeletedProject").mockResolvedValue({ success: true });

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "已删除项目" }));
    const dialog = await screen.findByRole("dialog", { name: "已删除项目" });
    await user.click(await within(dialog).findByRole("button", { name: "彻底删除「Old」" }));
    const confirm = await screen.findByRole("alertdialog", { name: "彻底删除「Old」？" });
    expect(API.purgeDeletedProject).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "彻底删除" }));

    expect(API.purgeDeletedProject).toHaveBeenCalledWith("demo#deleted-20261020T080000Z");
    expect(await within(dialog).findByText("没有已删除项目")).toBeInTheDocument();
  });

  it("exports the chosen scope of a card's project", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("second", { title: "Second" })] });
    vi.spyOn(API, "requestExportToken").mockResolvedValue({
      download_token: "token",
      expires_in: 300,
      diagnostics: { blocking: [], auto_fixed: [], warnings: [] },
    });
    const download = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    renderPage();
    const user = await openCardMenu("Second");
    await user.click(await screen.findByRole("menuitem", { name: "导出" }));
    const dialog = await screen.findByRole("dialog", { name: "选择导出范围" });
    await user.click(within(dialog).getByRole("radio", { name: /全部数据/ }));
    await user.click(within(dialog).getByRole("button", { name: "导出" }));

    await waitFor(() => expect(API.requestExportToken).toHaveBeenCalledWith("second", "full"));
    expect(download).toHaveBeenCalled();
    // 导出顺利开始时不提示
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("goes straight into an imported project when the import is clean", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject").mockResolvedValue(importResult("imported-demo", { auto_fixed: [], warnings: [] }));

    const { container, location } = renderPage();
    await screen.findByText("还没有项目");
    const file = chooseZip(container);

    await waitFor(() => expect(location.history?.at(-1)).toBe("/app/projects/imported-demo"));
    expect(API.importProject).toHaveBeenCalledWith(file, "prompt", { confirmReport: false });
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("shows import diagnostics before entering the imported project", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject").mockResolvedValue(
      importResult("imported-demo", {
        auto_fixed: [{ code: "missing_clues_field", message: "segments[0]: 补全缺失字段 clues_in_segment" }],
        warnings: [{ code: "validation_warning", message: "发现未识别的附加文件/目录：extras" }],
      }),
    );

    const { container, location } = renderPage();
    await screen.findByText("还没有项目");
    chooseZip(container);

    expect(await screen.findByText("导入诊断")).toBeInTheDocument();
    expect(useAppStore.getState().toast?.text).toContain("自动修复");
    expect(location.history?.at(-1)).toBe("/app/projects");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(location.history?.at(-1)).toBe("/app/projects/imported-demo"));
  });

  it("lists the blocking problems when an import fails with diagnostics", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject").mockRejectedValue(
      importError("导入包校验失败", {
        diagnostics: {
          blocking: [{ code: "validation_error", message: "缺少 project.json" }],
          auto_fixable: [],
          warnings: [],
        },
      }),
    );

    const { container } = renderPage();
    await screen.findByText("还没有项目");
    chooseZip(container, "broken.zip");

    expect(await screen.findByText("导入失败诊断")).toBeInTheDocument();
    expect(screen.getByText("缺少 project.json")).toBeInTheDocument();
  });

  it("tells why an import failed when the response carries no diagnostics", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject").mockRejectedValue(
      importError("上传文件过大", { diagnostics: { blocking: [], auto_fixable: [], warnings: [] } }),
    );

    const { container } = renderPage();
    await screen.findByText("还没有项目");
    chooseZip(container, "huge.zip");

    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toBe("项目导入失败：上传文件过大");
    });
    expect(screen.queryByText("导入失败诊断")).not.toBeInTheDocument();
  });

  it("asks how to resolve a duplicate project ID, then imports under a new ID", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("demo", { title: "Demo" })] });
    vi.spyOn(API, "importProject")
      .mockRejectedValueOnce(importError("检测到项目编号冲突", { status: 409, conflict_project_name: "demo" }))
      .mockResolvedValueOnce(importResult("demo-renamed", { auto_fixed: [], warnings: [] }));

    const { container, location } = renderPage();
    await projectList();
    const file = chooseZip(container);

    const dialog = await screen.findByRole("alertdialog", { name: "项目 ID 已存在" });
    expect(dialog).toHaveTextContent("项目 ID「demo」已被现有项目占用");
    fireEvent.click(within(dialog).getByRole("button", { name: "自动重命名导入" }));

    await waitFor(() => expect(API.importProject).toHaveBeenNthCalledWith(2, file, "rename", { confirmReport: false }));
    await waitFor(() => expect(location.history?.at(-1)).toBe("/app/projects/demo-renamed"));
  });
  it("asks to confirm the upgrade report before the imported project appears", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject")
      .mockRejectedValueOnce(
        importError("导入包升级时调整了部分内容，请确认后再导入", {
          status: 409,
          confirmation_required: true,
          migration_report: UPGRADE_REPORT,
          migration_report_episodes: [{ episode: 3, title: "雨夜" }],
        }),
      )
      .mockResolvedValueOnce(importResult("demo", { auto_fixed: [], warnings: [] }));

    const { container, location } = renderPage();
    await screen.findByText("还没有项目");
    const file = chooseZip(container);

    const dialog = await screen.findByRole("alertdialog", { name: "导入前请确认" });
    expect(within(dialog).getByText("雨夜的剧本从 scripts/custom.json 移到 scripts/episode_3.json。")).toBeInTheDocument();
    expect(API.listProjects).toHaveBeenCalledTimes(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "确认导入" }));

    await waitFor(() => expect(API.importProject).toHaveBeenNthCalledWith(2, file, "prompt", { confirmReport: true }));
    await waitFor(() => expect(location.history?.at(-1)).toBe("/app/projects/demo"));
  });

  it("imports nothing when the upgrade report is cancelled", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject").mockRejectedValueOnce(
      importError("导入包升级时调整了部分内容，请确认后再导入", {
        status: 409,
        confirmation_required: true,
        migration_report: UPGRADE_REPORT,
      }),
    );

    const { container, location } = renderPage();
    await screen.findByText("还没有项目");
    chooseZip(container);

    const dialog = await screen.findByRole("alertdialog", { name: "导入前请确认" });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(API.importProject).toHaveBeenCalledTimes(1);
    expect(location.history?.at(-1)).toBe("/app/projects");
  });

  it("keeps the confirmation when the confirmed import then hits a duplicate project ID", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [summary("demo", { title: "Demo" })] });
    vi.spyOn(API, "importProject")
      .mockRejectedValueOnce(
        importError("导入包升级时调整了部分内容，请确认后再导入", {
          status: 409,
          confirmation_required: true,
          migration_report: UPGRADE_REPORT,
        }),
      )
      .mockRejectedValueOnce(importError("检测到项目编号冲突", { status: 409, conflict_project_name: "demo" }))
      .mockResolvedValueOnce(importResult("demo-renamed", { auto_fixed: [], warnings: [] }));

    const { container } = renderPage();
    await projectList();
    const file = chooseZip(container);

    fireEvent.click(
      within(await screen.findByRole("alertdialog", { name: "导入前请确认" })).getByRole("button", { name: "确认导入" }),
    );
    fireEvent.click(
      within(await screen.findByRole("alertdialog", { name: "项目 ID 已存在" })).getByRole("button", {
        name: "自动重命名导入",
      }),
    );

    await waitFor(() => expect(API.importProject).toHaveBeenNthCalledWith(3, file, "rename", { confirmReport: true }));
  });

  describe("when the user has left the lobby before the import finishes", () => {
    /** 选好文件后让导入请求悬起，卸载页面（离开大厅），再返回收尾导入请求的 resolve / reject。 */
    async function leaveWhileImporting() {
      vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
      let resolve!: (value: Awaited<ReturnType<typeof API.importProject>>) => void;
      let reject!: (error: Error) => void;
      vi.spyOn(API, "importProject").mockImplementation(
        () =>
          new Promise((res, rej) => {
            resolve = res;
            reject = rej;
          }),
      );
      const { container, location, unmount } = renderPage();
      await screen.findByText("还没有项目");
      chooseZip(container);
      await waitFor(() => expect(API.importProject).toHaveBeenCalledTimes(1));
      unmount();
      return { location, resolve: (v: Awaited<ReturnType<typeof API.importProject>>) => act(async () => resolve(v)), reject: (e: Error) => act(async () => reject(e)) };
    }

    it("does not navigate after a clean import, and offers to open the new project", async () => {
      const { location, resolve } = await leaveWhileImporting();
      await resolve(importResult("imported-demo", { auto_fixed: [], warnings: [] }));

      expect(location.history?.at(-1)).toBe("/app/projects");
      const toast = useAppStore.getState().toast;
      expect(toast?.tone).toBe("success");
      expect(toast?.text).toBe("项目「Imported Demo」已导入");
      expect(toast?.action?.label).toBe("打开项目");
      act(() => toast?.action?.onClick());
      expect(location.history?.at(-1)).toBe("/app/projects/imported-demo");
    });

    it("reports the auto-fixed issues in the toast instead of a dialog", async () => {
      const { location, resolve } = await leaveWhileImporting();
      await resolve(
        importResult("imported-demo", {
          auto_fixed: [{ code: "missing_clues_field", message: "补全缺失字段" }],
          warnings: [],
        }),
      );

      expect(location.history?.at(-1)).toBe("/app/projects");
      expect(useAppStore.getState().toast?.text).toContain("自动修复 1 个问题");
      expect(useAppStore.getState().toast?.action?.label).toBe("打开项目");
    });

    it("says the import failed and what to do next", async () => {
      const { reject } = await leaveWhileImporting();
      await reject(
        importError("导入包校验失败", {
          diagnostics: { blocking: [{ code: "validation_error", message: "缺少 project.json" }], auto_fixable: [], warnings: [] },
        }),
      );

      const toast = useAppStore.getState().toast;
      expect(toast?.tone).toBe("error");
      expect(toast?.text).toBe("项目导入失败：导入包校验失败。回到项目大厅可以重新导入。");
    });

    it("says the import did not happen because of a duplicate project ID", async () => {
      const { reject } = await leaveWhileImporting();
      await reject(importError("检测到项目编号冲突", { status: 409, conflict_project_name: "demo" }));

      const toast = useAppStore.getState().toast;
      expect(toast?.tone).toBe("warning");
      expect(toast?.text).toContain("项目未导入");
      expect(toast?.text).toContain("项目 ID「demo」已被现有项目占用");
    });

    it("says the import needs the upgrade report confirmed", async () => {
      const { reject } = await leaveWhileImporting();
      await reject(
        importError("导入包升级时调整了部分内容，请确认后再导入", {
          status: 409,
          confirmation_required: true,
          migration_report: UPGRADE_REPORT,
        }),
      );

      const toast = useAppStore.getState().toast;
      expect(toast?.tone).toBe("warning");
      expect(toast?.text).toContain("需要确认");
    });
  });
});
