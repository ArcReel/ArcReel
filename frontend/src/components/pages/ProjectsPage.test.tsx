import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function importError(message: string, extra: { status?: number; conflict_project_name?: string; diagnostics?: ImportFailureDiagnostics } = {}) {
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
    expect(screen.getByRole("status")).toHaveTextContent("加载项目列表...");
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
    expect(API.importProject).toHaveBeenCalledWith(file, "prompt");
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("shows import diagnostics before entering the imported project", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    vi.spyOn(API, "importProject").mockResolvedValue(
      importResult("imported-demo", {
        auto_fixed: [{ code: "missing_clues_field", message: "segments[0]: 补全缺失字段 clues_in_segment" }],
        warnings: [{ code: "validation_warning", message: "发现未识别的附加文件/目录: extras" }],
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

    await waitFor(() => expect(API.importProject).toHaveBeenNthCalledWith(2, file, "rename"));
    await waitFor(() => expect(location.history?.at(-1)).toBe("/app/projects/demo-renamed"));
  });
});
