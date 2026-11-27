import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { createReadStream } from "node:fs";

const API = "/api/v1";
const PNG = new URL("../../public/android-chrome-192x192.png", import.meta.url);
const RETIRED_DRAFT_LABELS = ["待修复草稿", "AI 修复", "丢弃草稿", "可编辑草稿"];

test.use({ extraHTTPHeaders: { "Accept-Language": "zh" } });

async function post<T>(request: APIRequestContext, path: string, data?: unknown) {
  const response = await request.post(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as T;
}

async function patch<T>(request: APIRequestContext, path: string, data: unknown) {
  const response = await request.patch(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return response.status() === 204 ? (null as T) : (await response.json() as T);
}

async function put<T>(request: APIRequestContext, path: string, data: unknown) {
  const response = await request.put(path, { data });
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as T;
}

async function configureFixtureProviders(request: APIRequestContext, baseURL: string) {
  await post(request, `${API}/onboarding/seen`, {});
  const cred = await post<{ id: number }>(request, `${API}/providers/gemini-aistudio/credentials`, {
    name: "Local text fixture",
    api_key: "local-smoke-key",
    base_url: `${baseURL}/__playwright/gemini`,
  });
  const activate = await request.post(`${API}/providers/gemini-aistudio/credentials/${cred.id}/activate`);
  expect(activate.ok(), await activate.text()).toBe(true);
  await patch(request, `${API}/providers/ark/config`, { api_key: "local-smoke-key" });
  await patch(request, `${API}/system/config`, {
    default_text_backend: "gemini-aistudio/gemini-3-flash-preview",
    default_video_backend_i2v: "ark/doubao-seedance-2-0-260128",
    default_video_backend_r2v: "ark/doubao-seedance-2-0-260128",
  });
}

async function queueTextResponses(request: APIRequestContext, project: string, responses: unknown[]) {
  await post(request, `/__playwright/text-responses/${project}`, {
    responses: responses.map((item) => (typeof item === "string" ? item : JSON.stringify(item))),
  });
}

async function createReferenceEpisode(request: APIRequestContext, project: string) {
  const partOne = "山雨初歇，林夕推开茶馆木窗，";
  const partTwo = "看见街角亮起一盏马灯。";
  const sourceText = `${partOne}${partTwo}`;
  await post(request, `${API}/projects`, {
    name: project,
    title: "参考生视频内容确认联验",
    content_mode: "narration",
    generation_mode: "reference_video",
  });
  await post(request, `${API}/projects/${project}/characters`, { name: "林夕", description: "茶馆老板娘。" });
  const upload = await request.post(`${API}/projects/${project}/upload/character`, {
    params: { name: "林夕" },
    multipart: { file: createReadStream(PNG) },
  });
  await expect(upload).toBeOK();
  await post(request, `${API}/projects/${project}/episodes`, { title: "第一集", source_text: sourceText });
  return { partOne, partTwo };
}

function watchProtocolHealth(page: Page) {
  const draftRequests: string[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];

  page.on("request", (req) => {
    const { pathname } = new URL(req.url());
    if (pathname.includes("/drafts")) draftRequests.push(`${req.method()} ${pathname}`);
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });

  return {
    async assertClean() {
      expect(draftRequests, "unexpected /drafts requests").toEqual([]);
      expect(consoleErrors, "unexpected console errors").toEqual([]);
      expect(pageErrors, "unexpected page errors").toEqual([]);
      for (const label of RETIRED_DRAFT_LABELS) {
        await expect(page.getByText(label, { exact: false })).toHaveCount(0);
      }
    },
  };
}

test("drama script review loads blocking issues inline, unlocks confirm after saving fixes, and materializes the formal script", async ({ page, request, baseURL }) => {
  const health = watchProtocolHealth(page);
  await configureFixtureProviders(request, baseURL!);

  const project = "live-drama-review";
  const sourceText = "山雨初歇，林夕推开茶馆木窗，望着青石街尽头亮起的马灯。";
  await post(request, `${API}/projects`, {
    name: project,
    title: "剧情内容确认联验",
    content_mode: "drama",
    generation_mode: "storyboard",
  });
  await post(request, `${API}/projects/${project}/characters`, { name: "林夕", description: "茶馆老板娘。" });
  await post(request, `${API}/projects/${project}/episodes`, { title: "第一集", source_text: sourceText });

  await queueTextResponses(request, project, [
    {
      title: "第一集",
      scenes: [
        {
          scene_id: "E1S01",
          scene_description: "雨后茶馆木窗前，林夕望着街角马灯。",
          duration_seconds: 4,
          segment_break: false,
          source_text: "这段改写文字并不在原文里。",
          characters_in_scene: ["林夕"],
          scenes: [],
          props: [],
          utterances: [{ kind: "dialogue", speaker: "林夕", text: "雨停了。" }],
        },
      ],
      new_assets: [],
    },
  ]);

  await page.goto(`/app/projects/${project}/episodes/1`);
  await page.getByRole("button", { name: "AI 规划", exact: true }).click();

  await expect(page.getByText("1 项确认阻断项")).toBeVisible({ timeout: 15_000 });
  const confirmButton = page.getByRole("button", { name: "确认并继续" });
  await expect(confirmButton).toBeDisabled();
  await health.assertClean();

  const sourceEditor = page.getByRole("textbox", { name: "原文锚（仅供追溯，不朗读）" });
  const workspaceUrl = `**/projects/${project}/workspace`;
  let releaseWorkspace!: () => void;
  const held = new Promise<void>((resolve) => { releaseWorkspace = resolve; });
  const fullWorkspaceTitle = "第一集 · 完整工作区";
  await page.route(workspaceUrl, async (route) => {
    const response = await route.fetch();
    const workspace = await response.json() as { project: { episodes: { title: string }[] } };
    workspace.project.episodes[0].title = fullWorkspaceTitle;
    await held;
    await route.fulfill({ response, json: workspace });
  }, { times: 1 });
  const fullWorkspaceHeading = page.getByRole("heading", { name: fullWorkspaceTitle, exact: true });
  try {
    await page.reload({ waitUntil: "domcontentloaded" });
    await sourceEditor.fill(sourceText);
    await expect(fullWorkspaceHeading).toHaveCount(0);
  } finally {
    releaseWorkspace();
  }
  await expect(fullWorkspaceHeading).toBeVisible();
  await expect(sourceEditor).toHaveValue(sourceText);
  const saveResponse = page.waitForResponse(
    (reply) =>
      reply.request().method() === "PUT" &&
      new URL(reply.url()).pathname === `${API}/projects/${project}/episodes/1/script-review/content`,
  );
  await page.getByRole("button", { name: "保存", exact: true }).click();
  expect((await saveResponse).ok()).toBe(true);

  await expect(page.getByText("确认阻断项")).toHaveCount(0);
  await expect(confirmButton).toBeEnabled();

  const workspacePath = `${API}/projects/${project}/workspace`;
  const beforeConfirm = await request.get(workspacePath, { params: { episodes: 1 } });
  await expect(beforeConfirm).toBeOK();
  const segmented = await beforeConfirm.json() as { summary: { episodes: { script_status: string }[] } };
  expect(segmented.summary.episodes[0].script_status).toBe("segmented");
  let committed = false;
  await page.route(`**${workspacePath}?episodes=1`, async (route) => {
    if (committed) await route.continue();
    else await route.fulfill({ json: segmented });
  });
  await page.route(`**/episodes/1/script-review/confirm`, async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    committed = true;
    await route.fulfill({ response });
  });

  const confirmResponse = page.waitForResponse(
    (reply) =>
      reply.request().method() === "POST" &&
      new URL(reply.url()).pathname === `${API}/projects/${project}/episodes/1/script-review/confirm`,
  );
  await confirmButton.click();
  expect((await confirmResponse).ok()).toBe(true);

  await expect(page.getByRole("tab", { name: "分镜" })).toHaveAttribute("aria-selected", "true", { timeout: 15_000 });
  await expect(page.getByRole("navigation", { name: "分镜列表" }).getByText("待编写", { exact: true })).toBeVisible();

  const projectResp = await request.get(`${API}/projects/${project}`);
  await expect(projectResp).toBeOK();
  expect(await projectResp.json()).toMatchObject({
    scripts: {
      "episode_1.json": {
        scenes: [expect.objectContaining({ source_text: sourceText, pending_authoring: true })],
      },
    },
  });
  await health.assertClean();
});

test("reference video script review resolves blocking issues inline and materializes pending video units", async ({ page, request, baseURL }) => {
  const health = watchProtocolHealth(page);
  await configureFixtureProviders(request, baseURL!);

  const project = "live-ref-review";
  const { partOne, partTwo } = await createReferenceEpisode(request, project);

  await queueTextResponses(request, project, [
    {
      units: [
        {
          duration_seconds: 4,
          source_text: "不在原文里的错误锚点。",
          text: "@[林夕] 推开茶馆木窗。",
        },
        {
          duration_seconds: 4,
          source_text: partTwo,
          text: "街角亮起一盏马灯。",
        },
      ],
      new_assets: [],
    },
  ]);

  await page.goto(`/app/projects/${project}/episodes/1`);
  await page.getByRole("button", { name: "AI 规划", exact: true }).click();

  await expect(page.getByText("1 项确认阻断项")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("非源文逐字子串")).toBeVisible();
  const confirmButton = page.getByRole("button", { name: "确认拆分，继续生成" });
  await expect(confirmButton).toBeDisabled();
  await health.assertClean();

  await page.getByRole("button", { name: "编辑文稿" }).first().click();
  await page.getByRole("textbox", { name: "视频单元 1 原文" }).fill(partOne);

  const saveResponse = page.waitForResponse(
    (reply) =>
      reply.request().method() === "PUT" &&
      new URL(reply.url()).pathname === `${API}/projects/${project}/episodes/1/script-review/content`,
  );
  await page.getByRole("button", { name: "保存", exact: true }).click();
  expect((await saveResponse).ok()).toBe(true);

  await expect(page.getByText("非源文逐字子串")).toHaveCount(0);
  await expect(confirmButton).toBeEnabled();

  const confirmResponse = page.waitForResponse(
    (reply) =>
      reply.request().method() === "POST" &&
      new URL(reply.url()).pathname === `${API}/projects/${project}/episodes/1/script-review/confirm`,
  );
  await confirmButton.click();
  expect((await confirmResponse).ok()).toBe(true);

  await expect(page.getByRole("tab", { name: "视频单元" })).toHaveAttribute("aria-selected", "true", { timeout: 15_000 });

  const unitsResp = await request.get(`${API}/projects/${project}/reference-videos/episodes/1/units`);
  await expect(unitsResp).toBeOK();
  expect(await unitsResp.json()).toMatchObject({
    units: [
      expect.objectContaining({ source_text: partOne, pending_authoring: true }),
      expect.objectContaining({ source_text: partTwo, pending_authoring: true }),
    ],
  });
  await health.assertClean();
});

test("reference video units persist partial prompt authoring outcome across reload and allow retrying failed units", async ({ page, request, baseURL }) => {
  const health = watchProtocolHealth(page);
  await configureFixtureProviders(request, baseURL!);

  const project = "live-ref-partial";
  const { partOne, partTwo } = await createReferenceEpisode(request, project);

  const reviewBase = `${API}/projects/${project}/episodes/1/script-review`;
  await put(request, `${reviewBase}/content`, {
    units: [
      { unit_id: "E1U1", duration_seconds: 4, source_text: partOne, text: "@[林夕] 推开茶馆木窗。" },
      { unit_id: "E1U2", duration_seconds: 4, source_text: partTwo, text: "街角亮起一盏马灯。" },
    ],
    new_assets: [],
  });
  await post(request, `${reviewBase}/confirm`, {});

  await queueTextResponses(request, project, [
    {
      units: [
        { unit_id: "E1U1", text: "晨光里 @[林夕] 推开木窗，雨气拂面。" },
        { unit_id: "E1U2", text: "@[未登记路人] 提着灯站在街角。" },
      ],
    },
    {
      units: [
        { unit_id: "E1U2", text: "@[未登记路人] 仍站在湿漉漉的街角。" },
      ],
    },
    {
      units: [
        { unit_id: "E1U2", text: "雨后街角，@[林夕] 望向摇曳的马灯。" },
      ],
    },
  ]);

  await page.goto(`/app/projects/${project}/episodes/1?view=board`);
  await expect(page.getByRole("tab", { name: "视频单元" })).toHaveAttribute("aria-selected", "true");

  await post(request, `${API}/projects/${project}/episodes/1/prompt-authoring`, {});

  const notice = page.getByRole("status", { name: "提示词编写部分完成" });
  await expect(notice).toBeVisible({ timeout: 15_000 });
  await expect(notice.getByText("已写回 1 个单元，1 个单元未写回。")).toBeVisible();
  await expect(notice.getByText("视频单元 2")).toBeVisible();
  await health.assertClean();

  await page.reload();
  await expect(notice).toBeVisible({ timeout: 15_000 });
  await expect(notice.getByText("已写回 1 个单元，1 个单元未写回。")).toBeVisible();
  await expect(notice.getByText("视频单元 2")).toBeVisible();

  await notice.getByRole("button", { name: "编辑", exact: true }).click();
  const editor = page.getByRole("combobox", { name: "视频单元提示词" });
  await expect(editor).toHaveValue("街角亮起一盏马灯。");

  await notice.getByRole("button", { name: "重试失败单元" }).click();
  await expect(notice).toBeHidden({ timeout: 15_000 });
  await expect(editor).toHaveValue("雨后街角，@[林夕] 望向摇曳的马灯。");
  await health.assertClean();
});

test("v20 project migrated to v21 moves retired invalid drafts to trash and proceeds through script review to production", async ({ page, request, baseURL }) => {
  const health = watchProtocolHealth(page);
  await configureFixtureProviders(request, baseURL!);

  const project = "live-v20-migrated";
  const sourceText = "山雨初歇，旧城青石板泛着微光。";
  await post(request, `${API}/projects`, {
    name: project,
    title: "v20 迁移联验项目",
    content_mode: "narration",
    generation_mode: "storyboard",
  });
  await post(request, `${API}/projects/${project}/episodes`, { title: "第一集", source_text: sourceText });
  await put(request, `${API}/projects/${project}/episodes/1/script-review/content`, {
    segments: [
      {
        segment_id: "E1S01",
        novel_text: sourceText,
        duration_seconds: 4,
        segment_break: false,
        characters_in_segment: [],
        scenes: [],
        props: [],
      },
    ],
    new_assets: [],
  });
  await post(request, `/__playwright/downgrade-v20/${project}`);

  await post(request, `${API}/projects/${project}/migration/retry`, {});

  const projectResp = await request.get(`${API}/projects/${project}`);
  await expect(projectResp).toBeOK();
  expect(await projectResp.json()).toMatchObject({
    project: { schema_version: 21 },
  });

  const trashResp = await request.get(`${API}/projects/${project}/trash`);
  await expect(trashResp).toBeOK();
  expect(await trashResp.json()).toMatchObject({
    records: [
      expect.objectContaining({
        files: ["drafts/episode_1/script_plan_segments.invalid.json"],
      }),
    ],
  });

  await page.goto(`/app/projects/${project}/episodes/1?view=plan`);
  await page.getByRole("button", { name: /^本次升级调整了 \d+ 项内容$/ }).click();
  const report = page.getByRole("dialog");
  await expect(report.getByText(/script_plan_segments\.invalid\.json/)).toBeVisible();
  await report.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByText("待确认", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "小说原文" })).toHaveValue(sourceText);

  const confirmButton = page.getByRole("button", { name: "确认并继续" });
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();
  await expect(page.getByRole("button", { name: "已确认" })).toBeDisabled({ timeout: 15_000 });

  await page.getByRole("tab", { name: "分镜" }).click();
  await expect(page.getByRole("tab", { name: "分镜" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("navigation", { name: "分镜列表" })).toBeVisible();
  await health.assertClean();
});
