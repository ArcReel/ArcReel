import { box, clearAgentOverlay } from "../support/region-helpers.ts";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { recorded } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 参考生视频项目的「视频单元」视图：单元列表（窄画布下是图标栏）、文稿编辑器与成片预览三栏。
const UNITS_PATH = "/app/projects/demo/episodes/1?view=board";
const UNITS_API = "/api/v1/projects/demo/reference-videos/episodes/1/units";


interface RecordedProject {
  project: Record<string, unknown>;
}

const project = recorded<RecordedProject>("project-demo.json");

const unitId = (n: number) => `E1U${n}`;
const LONG_LINE =
  "@[林夕] 推开旧城茶馆二楼临街的雕花木窗，雨后的石板路泛着青光，巡夜人提着那盏蒙着油纸的旧马灯从箭楼下慢慢走过，灯影在湿漉漉的墙面上晃来晃去。";

// 压力数据：14 个单元，正文多行且有长句；第 1 个单元需重新规划，并带参考图分裂的阻断提示。
const UNITS = Array.from({ length: 14 }, (_, i) => ({
  unit_id: unitId(i + 1),
  text: [
    `镜头 ${i + 1}：${LONG_LINE}`,
    "林夕：「你来得太晚了，灯芯都快烧完了。」",
    i % 3 === 0 ? `${LONG_LINE}${LONG_LINE}` : "陈默低头看着青铜罗盘，指针一动不动。",
  ].join("\n"),
  duration_seconds: i % 2 === 0 ? 8 : 4,
  note: null,
  ...(i === 0 ? { needs_replan: true } : {}),
}));

function capability(id: string, overrides: Record<string, unknown> = {}) {
  return {
    unit_id: id,
    declared_capability: "i2v",
    hydrated_capability: "i2v",
    declared_references: [],
    unavailable_references: [],
    unregistered_references: [],
    allowed_durations: [4, 8],
    excluded_durations: {},
    duration_endpoint_fixed: false,
    duration_endpoint_fixed_reason: null,
    problem: null,
    problems: [],
    ...overrides,
  };
}

const CAPABILITIES = Object.fromEntries(
  UNITS.map((unit, i) => [
    unit.unit_id,
    i === 0
      ? capability(unit.unit_id, {
          declared_capability: "r2v",
          declared_references: [{ type: "character", name: "林夕" }],
          unavailable_references: [{ type: "character", name: "林夕" }],
          problems: [
            {
              code: "reference_asset_missing",
              blocking: true,
              unit_id: unit.unit_id,
              locations: [],
              params: { missing: [["character", "林夕"]] },
              action: "repair_reference_assets",
            },
          ],
        })
      : capability(unit.unit_id),
  ]),
);

/** 把单元移到 afterId 之后（null 移到最前）后的列表。 */
function movedUnits(id: string, afterId: string | null) {
  const rest = UNITS.filter((unit) => unit.unit_id !== id);
  const moving = UNITS.find((unit) => unit.unit_id === id)!;
  const at = afterId === null ? 0 : rest.findIndex((unit) => unit.unit_id === afterId) + 1;
  return [...rest.slice(0, at), moving, ...rest.slice(at)];
}

const API: ApiOverrides = {
  // 项目事件流是 SSE，没有录制；按不可重试的状态拒绝，页面停在替换后的数据上。
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  "GET /api/v1/projects/demo": {
    status: 200,
    body: { ...project, project: { ...project.project, generation_mode: "reference_video" } },
  },
  [`GET ${UNITS_API}`]: { status: 200, body: { units: UNITS, unit_capabilities: CAPABILITIES } },
  "GET /api/v1/projects/demo/episodes/1/drafts": { status: 200, body: { episode: 1, drafts: [] } },
  [`POST ${UNITS_API}/E1U2/move`]: { status: 200, body: { units: movedUnits("E1U2", null) } },
};

// 文稿编辑器的高度下限（12rem）。
const EDITOR_MIN_HEIGHT = 192;

const workbench = (page: Page) => page.getByRole("tabpanel", { name: "视频单元" });
const editor = (page: Page) => page.getByRole("combobox", { name: "视频单元提示词" });
const previewFrame = (page: Page) => page.getByTestId("reference-preview-frame");


async function workbenchReady(page: Page) {
  await page.getByRole("tab", { name: "视频单元" }).waitFor();
  await editor(page).waitFor();
}

/** 紧凑档 Agent 面板盖在画布右侧：先收起再点画布右侧的控件。 */

/** 窄画布下只有图标栏；单元的预览叠在编辑器同一格，经「视频」子页签切换。 */
async function showPreview(page: Page) {
  const previewTab = page.getByRole("tab", { name: "视频", exact: true });
  if (await previewTab.isVisible()) await previewTab.click();
}

/** 编辑器与预览都不低于高度下限：编辑器 12rem，竖屏画框高 min(55dvh, 栏宽 × 16/9)。 */
async function expectHeightFloors(page: Page) {
  const viewport = page.viewportSize()!;
  // 量编辑框（含边框），输入框本身在边框之内
  expect((await box(editor(page).locator(".."))).height).toBeGreaterThanOrEqual(EDITOR_MIN_HEIGHT);
  await showPreview(page);
  const frame = await box(previewFrame(page));
  expect(frame.height).toBeGreaterThanOrEqual(EDITOR_MIN_HEIGHT);
  expect(frame.height).toBeLessThanOrEqual(viewport.height * 0.55 + 1);
  expect(frame.width / frame.height).toBeCloseTo(9 / 16, 1);
  const editorTab = page.getByRole("tab", { name: "脚本", exact: true });
  if (await editorTab.isVisible()) await editorTab.click();
}

defineRegionScenarios("参考视频工作台", [
  {
    name: "单元列表、文稿编辑器与竖屏预览，选中单元带重新规划与参考图分裂提示",
    path: UNITS_PATH,
    api: API,
    ready: workbenchReady,
    act: async (page) => {
      await expect(page.getByRole("tab", { name: "视频单元" })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("alert").filter({ hasText: "该单元需先重新规划，修复后才能生成。" })).toBeVisible();
      await expect(page.getByTestId("reference-split-alert")).toBeVisible();
      await expect(editor(page)).toHaveValue(UNITS[0].text);
      await expectHeightFloors(page);
      await expect(previewFrame(page)).toHaveAttribute("data-aspect", "9:16");
    },
    screenshot: { name: "reference-workbench", target: workbench },
  },
  {
    name: "1440×700 与 1280×720 下编辑器与预览不低于高度下限",
    path: UNITS_PATH,
    api: API,
    ready: workbenchReady,
    act: async (page) => {
      const original = page.viewportSize()!;
      for (const size of [
        { width: 1440, height: 700 },
        { width: 1280, height: 720 },
      ]) {
        await page.setViewportSize(size);
        await expectHeightFloors(page);
      }
      await page.setViewportSize(original);
    },
  },
  {
    name: "改了正文后出现内联提示条，生成按钮改为「保存并生成」",
    path: UNITS_PATH,
    api: API,
    ready: workbenchReady,
    act: async (page) => {
      await editor(page).fill(`${UNITS[1].text}\n补一句旁白。`);
      await expect(page.getByRole("button", { name: "放弃修改" })).toBeVisible();
      await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
      await showPreview(page);
      await expect(page.getByRole("button", { name: "保存并生成" })).toBeVisible();
    },
  },
  {
    name: "聚焦把手后用键盘把单元移到最前",
    path: UNITS_PATH,
    api: API,
    ready: workbenchReady,
    act: async (page) => {
      // 窄画布下列表收成图标栏，从图标栏展开完整列表再排序
      const handle = page.getByRole("button", { name: "调整「U2」的顺序" });
      if (!(await handle.isVisible())) {
        await clearAgentOverlay(page);
        await page.getByRole("button", { name: "展开列表" }).click();
      }
      const moved = page.waitForRequest(
        (request) => request.method() === "POST" && request.url().endsWith(`${UNITS_API}/E1U2/move`),
      );
      await handle.focus();
      await page.keyboard.press("Space");
      await expect(page.getByText("已拿起「U2」，位于第 2 项，共 14 项。")).toBeAttached();
      await page.keyboard.press("ArrowUp");
      await expect(page.getByText("「U2」移到第 1 项，共 14 项。")).toBeAttached();
      await page.keyboard.press("Space");
      expect((await moved).postDataJSON()).toEqual({ after_unit_id: null });
      await expect(page.getByRole("button", { name: /^调整「.+」的顺序$/ }).first()).toHaveAccessibleName(
        "调整「U2」的顺序",
      );
    },
  },
  {
    name: "移除单元前的确认弹层",
    path: UNITS_PATH,
    api: API,
    ready: workbenchReady,
    act: async (page) => {
      await clearAgentOverlay(page);
      await page.getByRole("button", { name: "移除单元" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByText("移除单元 U1？")).toBeVisible();
      await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    },
    screenshot: { name: "reference-workbench-remove", target: (page) => page.getByRole("alertdialog") },
  },
]);
