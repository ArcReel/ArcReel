import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { defineRegionScenarios } from "../support/scenarios.ts";
import { RECORDED_DIR, type RecordedResponse } from "../support/recorded.ts";
import { expect, type ApiOverrides } from "../support/test.ts";

// 「调用端点」分区：全出血档的二级栏（我的端点 | 内置 Tab、紧凑档图标栏）与端点详情
// （使用这个端点的模型、定义表单、端点测试，底部保存栏）。

const ENDPOINTS_PATH = "/app/settings?section=endpoints";
// 录制的端点目录只有内置端点；压力变体在它后面接上自定义端点，「内置」Tab 仍是真实数据。
const recordedCatalog = (
  JSON.parse(readFileSync(join(RECORDED_DIR, "custom-providers-endpoints.json"), "utf8")) as RecordedResponse
).body as { endpoints: unknown[] };

const LONG_NAME = "华东二区备用线路 · 可灵 2.1 Master 图生视频（按量计费，月底结算）";

function definition(index: number) {
  return {
    kind: "declarative",
    schema_version: "1.0.0",
    meta: { name: `${LONG_NAME} ${index}`, author: "团队共享", version: "1.2.0" },
    media_type: "video",
    auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
    submit: {
      method: "POST",
      url: "{{ base_url }}/v1/videos/image2video/very/long/path/segment/that/keeps/going",
      body: { model: "{{ model }}", prompt: "{{ prompt }}", duration: "{{ duration }}" },
      extract: { task_id: ["$.data.task_id"] },
    },
    poll: {
      method: "GET",
      url: "{{ base_url }}/v1/videos/image2video/{{ task_id }}",
      extract: { status: ["$.data.task_status"], video_url: ["$.data.task_result.videos[0].url"] },
    },
    status_map: { succeed: "succeeded", failed: "failed" },
  };
}

const VALID = {
  status: 200,
  body: {
    errors: [],
    warnings: [],
    duplicates: [],
    hints: null,
    schema_version: { file: "1.0.0", current: "1.0.0", level: "direct" },
    min_app_version: null,
    import_shape: "endpoint_definition",
    wrapped_definition: null,
  },
};

// 压力变体：自定义端点多且名称长，二级栏在自己的栏里滚动；第一个端点被很多模型使用。
const MANY_ENDPOINTS: ApiOverrides = {
  "GET /api/v1/custom-endpoints": {
    status: 200,
    body: {
      endpoints: Array.from({ length: 18 }, (_, i) => ({
        installation: null,
        id: i + 1,
        key: `ce-${i + 1}`,
        display_name: `${LONG_NAME} ${i + 1}`,
        kind: "declarative",
        schema_version: "1.0.0",
        media_type: "video",
        definition: definition(i + 1),
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      })),
    },
  },
  "GET /api/v1/custom-providers": {
    status: 200,
    body: {
      providers: Array.from({ length: 4 }, (_, p) => ({
        id: p + 1,
        display_name: `自建 OpenAI 兼容网关 ${p + 1} · 华东二区备用线路（按量计费）`,
        discovery_format: "openai",
        base_url: `https://gateway-${p + 1}.example.com/v1`,
        api_key_masked: "sk-****",
        created_at: "2026-09-01T00:00:00Z",
        image_max_workers: null,
        video_max_workers: null,
        audio_max_workers: null,
        models: Array.from({ length: 3 }, (_, m) => ({
          id: p * 10 + m + 1,
          model_id: `kling-v2-1-master-image2video-extended-${p + 1}-${m + 1}`,
          display_name: `Kling ${p + 1}-${m + 1}`,
          endpoint: "ce-1",
          is_default: m === 0,
          is_enabled: true,
          price_unit: null,
          price_input: null,
          price_output: null,
          currency: null,
          supported_durations: null,
          resolution: null,
          max_output_tokens: null,
          system_capabilities: null,
          capability_overrides: null,
          global_bucket_refs: null,
        })),
      })),
    },
  },
  "GET /api/v1/custom-providers/endpoints": {
    status: 200,
    body: {
      endpoints: [
        ...recordedCatalog.endpoints,
        ...Array.from({ length: 18 }, (_, i) => ({
        key: `ce-${i + 1}`,
        media_type: "video",
        family: "custom",
        kind: "declarative",
        source: "custom",
        display_name_key: "",
        display_name: `${LONG_NAME} ${i + 1}`,
        request_method: "POST",
        request_path_template: "/v1/videos/image2video",
        image_capabilities: null,
        end_image_capable: false,
        size_fixed: false,
        duration_fixed: false,
        duration_frame_rate_missing: false,
        duration_tier_empty: false,
        native_resolution: null,
      })),
      ],
    },
  },
  // 详情编辑期持续校验：已保存的端点判重时排除自己（exclude_id），新建端点不带参数。
  "POST /api/v1/custom-endpoints/validate?exclude_id=1": VALID,
  "POST /api/v1/custom-endpoints/validate": VALID,
};

const rail = (page: Page) => page.getByRole("navigation", { name: "调用端点列表" });

async function detailReady(page: Page) {
  await page.getByRole("heading", { name: `${LONG_NAME} 1` }).waitFor();
  await page.getByRole("region", { name: "使用这个端点的模型" }).waitFor();
}

async function animationsSettled(page: Page, role: "dialog" | "alertdialog" | "menu") {
  // 等进场过渡结束再探测：淡入途中的半透明文字会被 axe 判为对比度不足
  await page
    .getByRole(role)
    .evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
}

defineRegionScenarios("调用端点", [
  {
    name: "我的端点详情：模型多、定义长时正文在详情栏里滚动，底部保存栏始终可见",
    path: `${ENDPOINTS_PATH}&endpoint=ce-1`,
    api: MANY_ENDPOINTS,
    ready: detailReady,
    act: async (page) => {
      const save = page.getByRole("button", { name: "保存", exact: true });
      await expect(save).toBeDisabled();
      const name = page.getByRole("main").getByRole("textbox").first();
      await name.fill(`${LONG_NAME} 1（已改）`);
      await page.getByText("提交生成任务").scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport({ ratio: 1 });
      await expect(save).toBeEnabled();
    },
    screenshot: { name: "endpoints-detail", target: (page) => page.getByRole("main") },
  },
  {
    name: "内置端点详情：页头并排「复制为我的端点」与主按钮「新建供应商并使用」，都在视口内",
    path: `${ENDPOINTS_PATH}&endpoint=newapi-video`,
    ready: async (page) => {
      await page.getByRole("heading", { name: "NewAPI Video" }).waitFor();
      await page.getByRole("region", { name: "使用这个端点的模型" }).waitFor();
    },
    act: async (page) => {
      await expect(page.getByRole("button", { name: "复制为我的端点" })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "新建供应商并使用" })).toBeInViewport({ ratio: 1 });
    },
    screenshot: { name: "endpoints-builtin-detail", target: (page) => page.getByRole("main") },
  },
  {
    name: "自定义端点多且名称长：二级栏滚动到底，「新建端点」在视口内",
    path: `${ENDPOINTS_PATH}&endpoint=ce-1`,
    api: MANY_ENDPOINTS,
    ready: detailReady,
    act: async (page) => {
      await rail(page).evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
      await expect(rail(page).getByRole("link", { name: /新建端点/ }).filter({ visible: true })).toBeInViewport();
    },
    screenshot: { name: "endpoints-rail", target: rail },
  },
  {
    name: "打开页头「更多操作」菜单，菜单项留在视口内",
    path: `${ENDPOINTS_PATH}&endpoint=ce-1`,
    api: MANY_ENDPOINTS,
    ready: detailReady,
    act: async (page) => {
      await page.getByRole("button", { name: "更多操作" }).click();
      const menu = page.getByRole("menu");
      await expect(menu).toBeInViewport({ ratio: 1 });
      await expect(menu.getByRole("menuitem", { name: "删除端点" })).toBeInViewport({ ratio: 1 });
      await animationsSettled(page, "menu");
    },
  },
  {
    name: "删除确认：焦点在「取消」，按钮留在视口内",
    path: `${ENDPOINTS_PATH}&endpoint=ce-1`,
    api: MANY_ENDPOINTS,
    ready: detailReady,
    act: async (page) => {
      await page.getByRole("button", { name: "更多操作" }).click();
      await page.getByRole("menuitem", { name: "删除端点" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
      await expect(dialog.getByRole("button", { name: "删除" })).toBeInViewport({ ratio: 1 });
      await animationsSettled(page, "alertdialog");
    },
    screenshot: { name: "endpoints-delete-dialog", target: (page) => page.getByRole("alertdialog") },
  },
  {
    name: "新建端点页打开导入对话框，标题与操作按钮留在视口内",
    path: `${ENDPOINTS_PATH}&endpoint=new`,
    api: MANY_ENDPOINTS,
    ready: async (page) => {
      await page.getByRole("button", { name: "导入定义" }).waitFor();
    },
    act: async (page) => {
      await page.getByRole("button", { name: "导入定义" }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeInViewport({ ratio: 1 });
      await expect(dialog.getByRole("button", { name: "取消" })).toBeInViewport({ ratio: 1 });
      await animationsSettled(page, "dialog");
    },
    screenshot: { name: "endpoints-import-dialog", target: (page) => page.getByRole("dialog") },
  },
]);
