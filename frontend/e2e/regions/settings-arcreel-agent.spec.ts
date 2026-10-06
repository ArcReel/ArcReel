import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 全局设置「ArcReel Agent」：Agent 供应商列表、默认折叠的运行参数、添加与删除 Agent 供应商的弹层。

const PATH = "/app/settings?section=arcreel-agent";

// 压力变体：Agent 供应商多、名称与地址长，每条都设置了全部模型路由。
const MANY_AGENT_PROVIDERS: ApiOverrides = {
  "GET /api/v1/agent/credentials": {
    status: 200,
    body: {
      credentials: Array.from({ length: 8 }, (_, i) => ({
        id: i + 1,
        preset_id: "__custom__",
        display_name: `自建 Anthropic 兼容网关 ${i + 1} · 华东二区备用线路（按量计费，仅限内部项目使用）`,
        icon_key: null,
        base_url: `https://anthropic-gateway-${i + 1}.internal.example.com/v1/proxy/claude-compatible/messages-endpoint`,
        api_key_masked: "sk-ant-****…a1b2",
        model: "claude-sonnet-4-5-20250929-extended-context-preview",
        haiku_model: "claude-haiku-4-5-20251001",
        sonnet_model: "claude-sonnet-4-5-20250929",
        opus_model: "claude-opus-4-1-20250805",
        subagent_model: "claude-haiku-4-5-20251001",
        is_active: i === 0,
        created_at: "2026-09-01T00:00:00Z",
      })),
    },
  },
};

async function sectionReady(page: Page) {
  await page.getByRole("heading", { name: "Agent 供应商" }).waitFor();
}

defineRegionScenarios("全局设置 · ArcReel Agent", [
  {
    name: "没有 Agent 供应商时，本分区就地提示内嵌 Agent 未配置",
    path: PATH,
    ready: async (page) => {
      await sectionReady(page);
      await page.getByText("ArcReel Agent 尚未配置").waitFor();
      await page.getByText(/还没有 Agent 供应商/).waitFor();
    },
    screenshot: { name: "settings-arcreel-agent", target: (page) => page.getByRole("region", { name: "ArcReel Agent" }) },
  },
  {
    name: "Agent 供应商多且名称长时展开高级运行参数，修改后外壳底行的保存按钮可见可用",
    path: PATH,
    api: MANY_AGENT_PROVIDERS,
    ready: async (page) => {
      await sectionReady(page);
      await page.getByRole("listitem", { name: /网关 8 ·/ }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: "高级" }).click();
      const field = page.getByRole("spinbutton", { name: "最大并发会话数" });
      await expect(field).toBeEnabled();
      await field.fill("8");
      const main = page.getByRole("main");
      await main.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(main.getByRole("link", { name: "提示词模版" })).toBeInViewport();
      await expect(page.getByRole("button", { name: "保存", exact: true })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
    },
    screenshot: { name: "settings-arcreel-agent-providers", target: (page) => page.getByRole("listitem", { name: /网关 1 ·/ }) },
  },
  {
    name: "添加 Agent 供应商对话框展开模型路由后，内容可滚动到底，底部按钮始终可见",
    path: PATH,
    ready: sectionReady,
    act: async (page) => {
      await page.getByRole("button", { name: "添加供应商" }).click();
      const dialog = page.getByRole("dialog", { name: "添加供应商" });
      await dialog.getByRole("button", { name: "高级模型路由" }).click();
      const last = dialog.getByRole("combobox", { name: "子智能体模型" });
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "测试连接" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "settings-arcreel-agent-dialog", target: (page) => page.getByRole("dialog") },
  },
  {
    name: "删除 Agent 供应商前弹出确认，说明删除的对象",
    path: PATH,
    api: MANY_AGENT_PROVIDERS,
    ready: async (page) => {
      await sectionReady(page);
      await page.getByRole("listitem", { name: /网关 2 ·/ }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("listitem", { name: /网关 2 ·/ }).getByRole("button", { name: "删除" }).click();
      const dialog = page.getByRole("alertdialog", { name: "删除 Agent 供应商" });
      await expect(dialog).toContainText("自建 Anthropic 兼容网关 2");
      await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    },
  },
]);
