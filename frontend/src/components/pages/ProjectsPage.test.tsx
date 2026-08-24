import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import type { Phase } from "@/types";

vi.mock("@/components/pages/CreateProjectModal", () => ({
  CreateProjectModal: () => <div data-testid="create-project-modal">Create Project Modal</div>,
}));

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

describe("ProjectsPage", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows loading state while projects are being fetched", () => {
    vi.spyOn(API, "listProjects").mockImplementation(
      () => new Promise(() => {}),
    );

    renderPage();
    expect(screen.getByText("加载项目列表...")).toBeInTheDocument();
  });

  it("shows empty state when no projects exist", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });

    renderPage();

    // 0 项目时仅渲染 NewProjectTile 占位卡（lobby_new_project_title）
    expect(await screen.findByText("新建项目")).toBeInTheDocument();
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
        configured_keys: ["api_key"],
        missing_keys: [],
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

    await screen.findByText("新建项目");
    expect(screen.queryByLabelText("配置未完成")).not.toBeInTheDocument();
  });

  it("renders project cards when data exists", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "demo",
          title: "Demo Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: {
            phase: "production",
            phase_progress: 0.5,
            needs_repair: false,
            repair_reason: null,
            assets: {
              character: { total: 2, available: 2, stale: 0 },
              scene: { total: 1, available: 1, stale: 0 },
              prop: { total: 1, available: 0, stale: 0 },
            },
            episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
          },
        },
      ],
    });

    renderPage();

    // Title may render twice (cinemascope poster overlay + heading) in the
    // featured "Now Editing" card — see ProjectsPage.tsx Darkroom design.
    expect((await screen.findAllByText("Demo Project")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("商业动画 京都").length).toBeGreaterThan(0);
    // 阶段名与工作台同一套词：卡片胶囊、筛选胶囊、Hero 计数格都读「制作」
    expect(screen.getAllByText("制作").length).toBeGreaterThan(0);
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("filters by the four merged phases and counts each pill", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "writing",
          title: "Writing Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: {
            phase: "script" as const,
            phase_progress: 0.5,
            needs_repair: false,
            repair_reason: null,
            assets: { character: { total: 1, available: 1, stale: 0 } },
            episodes_summary: { total: 2, scripted: 1, in_production: 0, completed: 0 },
          },
        },
        {
          name: "shooting",
          title: "Shooting Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: {
            phase: "production" as const,
            phase_progress: 0.4,
            needs_repair: false,
            repair_reason: null,
            assets: { character: { total: 1, available: 1, stale: 0 } },
            episodes_summary: { total: 2, scripted: 2, in_production: 1, completed: 0 },
          },
        },
      ],
    });

    renderPage();

    const scriptPill = await screen.findByRole("button", { name: /脚本/ });
    fireEvent.click(scriptPill);

    await waitFor(() => {
      expect(screen.queryByText("Shooting Project")).not.toBeInTheDocument();
    });
    expect(screen.getAllByText("Writing Project").length).toBeGreaterThan(0);
  });

  it("tells the reader how many sheets are older than the current content", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "aged",
          title: "Aged Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: {
            phase: "production" as const,
            phase_progress: 0.5,
            needs_repair: false,
            repair_reason: null,
            assets: {
              character: { total: 3, available: 3, stale: 2 },
              scene: { total: 1, available: 1, stale: 0 },
              prop: { total: 0, available: 0, stale: 0 },
              // 卡片的计数格只列举三类，这一行仍要把其余资产类型的 stale 算进去
              product: { total: 1, available: 1, stale: 1 },
            },
            episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
          },
        },
      ],
    });

    renderPage();

    expect(await screen.findByText("3 张资产图比当前内容旧")).toBeInTheDocument();
    // stale 仍是可用产物：计数格照报 3 / 3，不从可用里扣
    expect(screen.getAllByText("3 / 3").length).toBeGreaterThan(0);
  });

  it("marks a project that needs repair and shows the reason on the card", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "broken",
          title: "Broken Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: {
            phase: "production",
            phase_progress: 0.5,
            needs_repair: true,
            repair_reason: "episode script scripts/episode_1.json item 2 has no identity",
            assets: {
              character: { total: 1, available: 1, stale: 0 },
              scene: { total: 1, available: 1, stale: 0 },
              prop: { total: 0, available: 0, stale: 0 },
            },
            episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
          },
        },
      ],
    });

    renderPage();

    // 唯一项目会成为「正在编辑」卡；标记与原因在两张卡上都必须出现
    expect((await screen.findAllByText("需要修复")).length).toBeGreaterThan(0);
    // 原因是可见文本而非 tooltip：触摸设备打不开 title，屏幕阅读器也读不到
    expect(
      screen.getAllByText("episode script scripts/episode_1.json item 2 has no identity").length,
    ).toBeGreaterThan(0);
  });

  it("puts the repair state and reason into the library card's accessible name", async () => {
    const brokenStatus = {
      phase: "production" as const,
      phase_progress: 0.5,
      needs_repair: true,
      repair_reason: "episode script scripts/episode_1.json item 2 has no identity",
      assets: {
        character: { total: 1, available: 1, stale: 0 },
        scene: { total: 1, available: 1, stale: 0 },
        prop: { total: 0, available: 0, stale: 0 },
      },
      episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
    };
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "healthy",
          title: "Healthy Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: { ...brokenStatus, needs_repair: false, repair_reason: null, phase_progress: 0.9 },
        },
        {
          name: "broken",
          title: "Broken Project",
          style: "Anime",
          style_template_id: "anim_kyoto",
          thumbnail: null,
          status: brokenStatus,
        },
      ],
    });

    renderPage();

    // 常规卡整张是一个 link，内部文本被 aria-label 覆盖——修复状态与原因必须写进这个名字
    expect(
      await screen.findByRole("link", {
        name: /Broken Project.*需要修复.*episode script scripts\/episode_1\.json item 2 has no identity/s,
      }),
    ).toBeInTheDocument();
  });

  it("shows 自定义风格 label when project has style_image but no template_id", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "demo",
          title: "Custom Demo",
          style: "",
          style_template_id: null,
          style_image: "style_reference.png",
          thumbnail: null,
          status: {
            phase: "production",
            phase_progress: 0.1,
            needs_repair: false,
            repair_reason: null,
            assets: {
              character: { total: 1, available: 0, stale: 0 },
              scene: { total: 0, available: 0, stale: 0 },
              prop: { total: 0, available: 0, stale: 0 },
            },
            episodes_summary: { total: 1, scripted: 0, in_production: 1, completed: 0 },
          },
        },
      ],
    });

    renderPage();

    expect((await screen.findAllByText("Custom Demo")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/自定义风格/).length).toBeGreaterThan(0);
  });

  it("shows 未设置风格 label when project has neither template_id nor style_image", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        {
          name: "demo",
          title: "Empty Style Demo",
          style: "",
          style_template_id: null,
          style_image: null,
          thumbnail: null,
          status: {
            phase: "production",
            phase_progress: 0,
            needs_repair: false,
            repair_reason: null,
            assets: {
              character: { total: 0, available: 0, stale: 0 },
              scene: { total: 0, available: 0, stale: 0 },
              prop: { total: 0, available: 0, stale: 0 },
            },
            episodes_summary: { total: 0, scripted: 0, in_production: 0, completed: 0 },
          },
        },
      ],
    });

    renderPage();

    expect((await screen.findAllByText("Empty Style Demo")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/未设置风格/).length).toBeGreaterThan(0);
  });

  it("opens create project modal after clicking new project button", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });

    renderPage();
    await screen.findByText("新建项目");
    expect(screen.queryByTestId("create-project-modal")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));

    await waitFor(() => {
      expect(screen.getByTestId("create-project-modal")).toBeInTheDocument();
    });
  });

  it("imports a zip project, refreshes the list, and navigates to the workspace", async () => {
    vi.spyOn(API, "listProjects")
      .mockResolvedValueOnce({ projects: [] })
      .mockResolvedValueOnce({
        projects: [
          {
            name: "imported-demo",
            title: "Imported Demo",
            style: "Anime",
            thumbnail: null,
            status: {
              phase: "completed",
              phase_progress: 1,
              needs_repair: false,
              repair_reason: null,
              assets: {
                character: { total: 1, available: 1, stale: 0 },
                scene: { total: 1, available: 1, stale: 0 },
                prop: { total: 0, available: 0, stale: 0 },
              },
              episodes_summary: { total: 1, scripted: 1, in_production: 0, completed: 1 },
            },
          },
        ],
      });
    vi.spyOn(API, "importProject").mockResolvedValue({
      success: true,
      project_name: "imported-demo",
      project: {
        title: "Imported Demo",
        content_mode: "narration",
        style: "Anime",
        episodes: [],
        characters: {},
        scenes: {},
        props: {},
      },
      warnings: ["发现未识别的附加文件/目录: extras"],
      conflict_resolution: "none",
      diagnostics: {
        auto_fixed: [{ code: "missing_clues_field", message: "segments[0]: 补全缺失字段 clues_in_segment" }],
        warnings: [{ code: "validation_warning", message: "发现未识别的附加文件/目录: extras" }],
      },
    });

    const { container, location } = renderPage();
    await screen.findByText("新建项目");

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["zip"], "project.zip", { type: "application/zip" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(API.importProject).toHaveBeenCalledWith(file, "prompt");
    });
    // 当存在 warnings/auto_fixed 时先弹诊断对话框，关闭后才跳转
    expect(await screen.findByText("导入诊断")).toBeInTheDocument();
    expect(useAppStore.getState().toast?.text).toContain("自动修复");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(location.history?.at(-1)).toBe("/app/projects/imported-demo");
    });
  });

  it("shows a structured toast when import fails", async () => {
    vi.spyOn(API, "listProjects").mockResolvedValue({ projects: [] });
    const error = new Error("导入包校验失败") as Error & {
      detail?: string;
      errors?: string[];
      warnings?: string[];
      diagnostics?: {
        blocking: { code: string; message: string }[];
        auto_fixable: { code: string; message: string }[];
        warnings: { code: string; message: string }[];
      };
    };
    error.detail = "导入包校验失败";
    error.errors = ["缺少 project.json", "缺少 scripts/episode_1.json", "缺少角色图"];
    error.warnings = ["发现未识别的附加文件/目录: extras"];
    error.diagnostics = {
      blocking: [
        { code: "validation_error", message: "缺少 project.json" },
        { code: "validation_error", message: "缺少 scripts/episode_1.json" },
      ],
      auto_fixable: [
        { code: "missing_clues_field", message: "segments[0]: 补全缺失字段 clues_in_segment" },
      ],
      warnings: [
        { code: "validation_warning", message: "发现未识别的附加文件/目录: extras" },
      ],
    };
    vi.spyOn(API, "importProject").mockRejectedValue(error);

    const { container } = renderPage();
    await screen.findByText("新建项目");

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["zip"], "broken.zip", { type: "application/zip" })] },
    });

    await waitFor(() => {
      expect(screen.getByText("导入失败诊断")).toBeInTheDocument();
    });
    expect(screen.getByText("缺少 project.json")).toBeInTheDocument();
    expect(screen.getByText("缺少 scripts/episode_1.json")).toBeInTheDocument();
    expect(screen.getByText("segments[0]: 补全缺失字段 clues_in_segment")).toBeInTheDocument();
  });

  it("opens a secondary confirmation when import hits a duplicate project id", async () => {
    vi.spyOn(API, "listProjects")
      .mockResolvedValueOnce({ projects: [] })
      .mockResolvedValueOnce({
        projects: [
          {
            name: "demo",
            title: "Demo",
            style: "Anime",
            thumbnail: null,
            status: {
              phase: "completed",
              phase_progress: 1,
              needs_repair: false,
              repair_reason: null,
              assets: {
                character: { total: 1, available: 1, stale: 0 },
                scene: { total: 1, available: 1, stale: 0 },
                prop: { total: 0, available: 0, stale: 0 },
              },
              episodes_summary: { total: 1, scripted: 1, in_production: 0, completed: 1 },
            },
          },
        ],
      });
    const conflictError = new Error("检测到项目编号冲突") as Error & {
      status?: number;
      detail?: string;
      errors?: string[];
      conflict_project_name?: string;
    };
    conflictError.status = 409;
    conflictError.detail = "检测到项目编号冲突";
    conflictError.errors = ["项目编号 'demo' 已存在"];
    conflictError.conflict_project_name = "demo";

    vi.spyOn(API, "importProject")
      .mockRejectedValueOnce(conflictError)
      .mockResolvedValueOnce({
        success: true,
        project_name: "demo-renamed",
        project: {
          title: "Renamed Demo",
          content_mode: "narration",
          style: "Anime",
          episodes: [],
          characters: {},
          scenes: {},
          props: {},
        },
        warnings: [],
        conflict_resolution: "renamed",
        diagnostics: {
          auto_fixed: [],
          warnings: [],
        },
      });

    const { container, location } = renderPage();
    await screen.findByText("新建项目");

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["zip"], "project.zip", { type: "application/zip" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByText("检测到项目编号重复")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "自动重命名导入" }));

    await waitFor(() => {
      expect(API.importProject).toHaveBeenNthCalledWith(1, file, "prompt");
    });
    await waitFor(() => {
      expect(API.importProject).toHaveBeenNthCalledWith(2, file, "rename");
    });
    await waitFor(() => {
      expect(location.history?.at(-1)).toBe("/app/projects/demo-renamed");
    });
  });

  it("breaks the hero counts down over all four phases", async () => {
    const project = (name: string, phase: Phase) => ({
      name,
      title: name,
      style: "Anime",
      thumbnail: null,
      status: {
        phase,
        phase_progress: 0,
        needs_repair: false,
        repair_reason: null,
        assets: {
          character: { total: 0, available: 0, stale: 0 },
          scene: { total: 0, available: 0, stale: 0 },
          prop: { total: 0, available: 0, stale: 0 },
        },
        episodes_summary: { total: 0, scripted: 0, in_production: 0, completed: 0 },
      },
    });
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [
        project("prep-a", "preparation"),
        project("prep-b", "preparation"),
        project("scripted", "script"),
        project("filming", "production"),
        project("done", "completed"),
      ],
    });

    renderPage();

    // 每个阶段都要有自己的一格：新建项目落在「准备」，不能只汇进总数就消失。
    const hero = await screen.findByTestId("lobby-hero-stats");
    const cells = Array.from(hero.children).map((cell) => cell.textContent);
    expect(cells).toEqual(["项目5", "准备2", "脚本1", "制作1", "完成1"]);
  });
});
