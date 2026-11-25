import type { Page } from "@playwright/test";
import { clearAgentOverlay } from "../support/region-helpers.ts";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { workspaceOverride } from "../support/workspace-override.ts";
import { expect, test, type ApiOverrides } from "../support/test.ts";


test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } });

const UNITS_PATH = "/app/projects/demo/episodes/1?view=board";
const UNITS_API = "/api/v1/projects/demo/reference-videos/episodes/1/units";

const MENTION_HEAVY_TEXT = Array.from(
  { length: 80 },
  (_, i) =>
    `镜头 ${i + 1}（第 ${i * 4} 秒起 3.5s）：@[林夕] 与 @[陈默] 在 ${i + 2} 号街口相遇，` +
    `@[林夕/雨夜] 撑开第 ${i * 7 + 1} 把旧伞，@[陈默/便装] 数着 ${1024 + i * 37} 块青石板。`,
).join("\n");

const UNIT_ID = "E1U1";

const API: ApiOverrides = {
  "GET /api/v1/projects/demo/events/stream": { status: 404, body: { detail: "页面级套件不回放事件流" } },
  ...workspaceOverride({
    generation_mode: "reference_video",
    characters: {
      林夕: { description: "茶馆二楼的说书人。", derivatives: { 雨夜: { description: "披上蓑衣。" } } },
      陈默: { description: "巡夜人。", derivatives: { 便装: { description: "换下号服。" } } },
    },
  }),
  [`GET ${UNITS_API}`]: {
    status: 200,
    body: {
      units: [{ unit_id: UNIT_ID, text: MENTION_HEAVY_TEXT, duration_seconds: 8, note: null }],
      unit_capabilities: {
        [UNIT_ID]: {
          unit_id: UNIT_ID,
          declared_capability: "r2v",
          hydrated_capability: "r2v",
          declared_references: [],
          unavailable_references: [],
          unregistered_references: [],
          allowed_durations: [4, 8],
          excluded_durations: {},
          duration_endpoint_fixed: false,
          duration_endpoint_fixed_reason: null,
          problem: null,
          problems: [],
        },
      },
    },
  },
};

const editor = (page: Page) => page.getByRole("combobox", { name: "视频单元提示词" });

/** 用不带 scrollbar-gutter 的探针量滚动条宽度，与被测的两层无关；为 0 说明浏览器隐藏了滚动条。 */
async function measureScrollbarWidth(page: Page): Promise<number> {
  return page.evaluate(() => {
    const probe = document.createElement("div");
    probe.style.cssText = "position: fixed; width: 100px; height: 100px; overflow: scroll";
    document.body.append(probe);
    const width = probe.offsetWidth - probe.clientWidth;
    probe.remove();
    return width;
  });
}

async function requireVisibleScrollbars(page: Page) {
  const scrollbarWidth = await measureScrollbarWidth(page);
  expect(scrollbarWidth, "浏览器应显示占宽度的滚动条：无头模式须去掉 --hide-scrollbars").toBeGreaterThan(0);
}

interface LayerAlignment {
  textareaClientWidth: number;
  preClientWidth: number;
  overflowing: boolean;
  checked: number;
  mismatches: { offset: number; char: string; caretOffset: number | null }[];
}

async function measureLayerAlignment(page: Page): Promise<LayerAlignment> {
  return editor(page).evaluate((node) => {
    const textarea = node as HTMLTextAreaElement;
    const pre = textarea.parentElement!.querySelector("pre")!;
    const top = textarea.getBoundingClientRect().top + textarea.clientTop;
    const bottom = top + textarea.clientHeight;
    const mismatches: LayerAlignment["mismatches"] = [];
    let checked = 0;
    let offset = 0;
    const walker = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode() as Text | null; text; text = walker.nextNode() as Text | null) {
      const content = text.data;
      for (let i = 0; i < content.length; i += 1) {
        const char = content[i];
        // 换行符没有可点的字形；末尾的零宽占位只给换行结尾的正文撑出一行，不对应输入框里的字符。
        if (char === "\n" || char === "\u200b") continue;
        const range = document.createRange();
        range.setStart(text, i);
        range.setEnd(text, i + 1);
        const rect = range.getClientRects()[0];
        if (!rect || rect.width === 0) continue;
        const y = rect.top + rect.height / 2;
        if (y < top || y > bottom) continue;
        const caret = document.caretPositionFromPoint(rect.left + rect.width / 4, y);
        const caretOffset = caret?.offsetNode === textarea ? caret.offset : null;
        checked += 1;
        if (caretOffset !== offset + i && mismatches.length < 5) {
          mismatches.push({ offset: offset + i, char, caretOffset });
        }
      }
      offset += content.length;
    }
    return {
      textareaClientWidth: textarea.clientWidth,
      preClientWidth: pre.clientWidth,
      overflowing: textarea.scrollHeight > textarea.clientHeight,
      checked,
      mismatches,
    };
  });
}

async function expectLayersAligned(page: Page) {
  const alignment = await measureLayerAlignment(page);
  expect(alignment.overflowing, "压力文本应让输入框出现滚动条").toBe(true);
  expect(alignment.checked, "可视区内应有可比对的字符").toBeGreaterThan(100);
  expect(alignment.mismatches, "着色层字符位置与输入框光标偏移应逐字重合").toEqual([]);
  expect(alignment.preClientWidth, "两层可用宽度应一致").toBe(alignment.textareaClientWidth);
}

defineRegionScenarios("参考视频文稿编辑器", [
  {
    name: "含多个本体与衍生引用、数字的长文稿，滚动前后着色层与输入框逐字对齐",
    path: UNITS_PATH,
    api: API,
    viewports: "both",
    ready: async (page) => {
      await page.getByRole("tab", { name: "视频单元" }).waitFor();
      await editor(page).waitFor();
    },
    act: async (page) => {
      await requireVisibleScrollbars(page);
      await clearAgentOverlay(page);
      await expect(editor(page)).toHaveValue(MENTION_HEAVY_TEXT);
      await expectLayersAligned(page);
      const scrollTop = await editor(page).evaluate((el) => {
        el.scrollTop = el.scrollHeight;
        return el.scrollTop;
      });
      expect(scrollTop).toBeGreaterThan(0);
      const pre = editor(page).locator("xpath=preceding-sibling::pre");
      await expect.poll(() => pre.evaluate((el) => el.scrollTop)).toBe(scrollTop);
      await expectLayersAligned(page);
    },
  },
]);
