import { expect, test, type APIRequestContext, type APIResponse, type Locator, type Page } from "@playwright/test";
import { createReadStream } from "node:fs";

const API = "/api/v1";
const HERO = "Héro";
const PNG = new URL("../../public/android-chrome-192x192.png", import.meta.url);
const CONFLICT = "项目资产有任务正在排队或执行，无法覆盖。任务结束后再试。";

test.use({ extraHTTPHeaders: { "Accept-Language": "zh" } });

async function post<T>(request: APIRequestContext, path: string, data?: unknown) {
  const response = await request.post(path, { data });
  await expect(response).toBeOK();
  return await response.json() as T;
}

async function createProject(request: APIRequestContext, project: string, title: string, audioBackend?: string) {
  await post(request, `${API}/onboarding/seen`, {});
  await post(request, `${API}/projects`, {
    name: project, title, generation_mode: "storyboard", audio_backend: audioBackend,
  });
  return `${API}/projects/${project}`;
}

async function addCharacter(request: APIRequestContext, projectApi: string, name: string, sheet = false) {
  await post(request, `${projectApi}/characters`, { name, description: "一位博物馆讲解员。" });
  if (sheet) await expect(await uploadSheet(request, projectApi, name)).toBeOK();
  return `${projectApi}/characters/${encodeURIComponent(name)}`;
}

function uploadSheet(request: APIRequestContext, projectApi: string, name: string) {
  return request.post(`${projectApi}/upload/character`, {
    params: { name },
    multipart: { file: createReadStream(PNG) },
  });
}

async function recentIds(request: APIRequestContext, project: string) {
  const response = await request.get(`${API}/tasks`, { params: { project_name: project, page_size: 200 } });
  await expect(response).toBeOK();
  const { items } = await response.json() as { items: { task_id: string }[] };
  expect(items).toHaveLength(200);
  return items.map(({ task_id }) => task_id);
}

async function release(request: APIRequestContext, taskIds: string[], outcome: "success" | "failure" = "success") {
  await post(request, "/__playwright/release", { task_ids: taskIds, outcome });
}

async function submit<T>(button: Locator, path: string): Promise<T> {
  const [response] = await Promise.all([
    button.page().waitForResponse((reply) => reply.request().method() === "POST" && new URL(reply.url()).pathname === path),
    button.click(),
  ]);
  expect(response.ok(), await response.text()).toBe(true);
  return await response.json() as T;
}

async function expectRunningTogether(request: APIRequestContext, projectApi: string, ids: string[]) {
  await expect.poll(async () => {
    const response = await request.get(`${projectApi}/tasks/active`);
    await expect(response).toBeOK();
    const { items } = await response.json() as { items: { task_id: string; status: string }[] };
    const running = new Set(items.filter(({ status }) => status === "running").map(({ task_id }) => task_id));
    return ids.every((id) => running.has(id));
  }).toBe(true);
}

async function expectBusy(response: APIResponse) {
  expect(response.status()).toBe(409);
  expect(await response.json()).toMatchObject({ code: "asset_resource_busy" });
}

function assetMenu(page: Page, name: string) {
  return page.getByRole("button", { name: `「${name}」的更多操作` });
}

async function openCharacter(page: Page, name: string) {
  await page.getByRole("button", { name, exact: true }).click();
  return page.getByRole("dialog", { name });
}

function derivative(sheet: Locator, name: string) {
  return sheet.getByRole("listitem").filter({ has: sheet.page().getByRole("heading", { name, exact: true }) });
}

test("an older active image appears in usage and blocks asset writes while allowing alias edits and previews", async ({ page, request }) => {
  const project = "long-assets";
  const projectApi = await createProject(request, project, "长项目资产");
  const characterApi = await addCharacter(request, projectApi, HERO, true);
  const { task_id } = await post<{ task_id: string }>(request, `${projectApi}/generate/character/${encodeURIComponent(HERO)}`);
  await post(request, `/__playwright/history/${project}`);
  expect(await recentIds(request, project)).not.toContain(task_id);

  await page.goto(`/app/projects/${project}/characters`);
  await page.getByRole("button", { name: /^使用记录 · 参考费用/ }).click();
  const usage = page.getByRole("dialog", { name: "使用记录" });
  await expect(usage.getByRole("region", { name: "进行中" }).getByRole("img", { name: "图片" })).toBeVisible();
  await usage.getByRole("link", { name: "查看全部记录" }).click();
  await expect(page.getByRole("table").getByText("进行中 · 1", { exact: true })).toBeVisible();
  await page.goBack();

  await assetMenu(page, HERO).click();
  await expect(page.getByRole("menuitem", { name: "加入资产库" })).toBeDisabled();
  await page.keyboard.press("Escape");

  const nfd = HERO.normalize("NFD");
  await expectBusy(await uploadSheet(request, projectApi, nfd));
  const rename = { new_name: "新名字", dry_run: true };
  await post(request, `${characterApi}/rename`, rename);
  await expectBusy(await request.post(`${characterApi}/rename`, { data: { ...rename, dry_run: false } }));

  const sheet = await openCharacter(page, HERO);
  await sheet.getByRole("button", { name: "添加别名" }).click();
  const alias = page.getByRole("dialog").filter({ has: page.getByRole("textbox", { name: "别名" }) });
  await alias.getByRole("textbox", { name: "别名" }).fill("讲解员");
  await alias.getByRole("button", { name: "添加", exact: true }).click();
  await expect(sheet.getByRole("button", { name: "删除别名「讲解员」" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();

  await release(request, [task_id]);
  await assetMenu(page, HERO).click();
  const exportItem = page.getByRole("menuitem", { name: "加入资产库" });
  await expect(exportItem).toBeEnabled();
  await exportItem.click();
  await expect(page.getByRole("dialog", { name: `加入资产库：${HERO}` })).toBeVisible();
});

test("applying library assets reports a busy item and still applies an idle match", async ({ page, request }) => {
  const project = "apply-assets";
  const title = "覆盖目标";
  const projectApi = await createProject(request, project, title);
  const busy = "繁忙馆长";
  const idle = "空闲馆长";
  await addCharacter(request, projectApi, busy, true);
  await addCharacter(request, projectApi, idle, true);
  const ids = new Map<string, string>();
  for (const name of [busy, idle]) {
    const created = await request.post(`${API}/assets`, {
      multipart: { type: "character", name, description: "全局资产描述", image: createReadStream(PNG) },
    });
    await expect(created).toBeOK();
    const { asset } = await created.json() as { asset: { id: string } };
    ids.set(name, asset.id);
  }
  const { task_id } = await post<{ task_id: string }>(request, `${projectApi}/generate/character/${busy}`);

  await page.goto("/app/assets");
  await assetMenu(page, busy).click();
  await page.getByRole("menuitem", { name: "应用到项目…" }).click();
  const dialog = page.getByRole("dialog", { name: `把「${busy}」应用到项目` });
  await dialog.getByRole("combobox", { name: "目标项目" }).click();
  await page.getByRole("option", { name: title }).click();
  await dialog.getByRole("radio", { name: "覆盖项目里的同类资产" }).check();
  await dialog.getByRole("button", { name: "应用", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText(CONFLICT);

  const result = await post(request, `${API}/assets/apply-to-project`, {
    asset_ids: [ids.get(busy), ids.get(idle)], target_project: project, conflict_policy: "overwrite",
  });
  expect(result).toMatchObject({
    succeeded: [{ id: ids.get(idle), name: idle }],
    failed: [{ id: ids.get(busy), reason: "resource_busy" }],
  });
  const projectResponse = await request.get(projectApi);
  await expect(projectResponse).toBeOK();
  expect(await projectResponse.json()).toMatchObject({
    project: { characters: {
      [busy]: { description: "一位博物馆讲解员。" },
      [idle]: { description: "全局资产描述" },
    } },
  });
  await release(request, [task_id]);
});

test("sibling derivatives can run together and voice can finish off the first task page during base image generation", async ({ page, request }) => {
  const project = "derivative-voice";
  await post(request, `${API}/providers/gemini-aistudio/credentials`, { name: "Local image fixture", api_key: "local-smoke-key" });
  await post(request, `${API}/providers/minimax/credentials`, { name: "Local audio fixture", api_key: "local-smoke-key" });
  const projectApi = await createProject(request, project, "角色并行", "minimax/speech-2.8-turbo");
  const characterApi = await addCharacter(request, projectApi, HERO, true);
  const first = "Été";
  const second = "冬装";
  for (const name of [first, second]) {
    await post(request, `${characterApi}/derivatives`, { name, description: "换一套外观。" });
  }
  const generateBase = `${projectApi}/generate/character/${encodeURIComponent(HERO)}`;
  const generateDerivative = (owner: string, name: string) =>
    `${projectApi}/generate/character/${encodeURIComponent(owner)}/derivatives/${encodeURIComponent(name)}`;
  const a = await post<{ task_id: string }>(request, generateDerivative(HERO.normalize("NFD"), first.normalize("NFD")));
  await expectBusy(await request.post(generateBase));

  await page.goto(`/app/projects/${project}/characters`);
  const sheet = await openCharacter(page, HERO);
  await expect(derivative(sheet, first).getByRole("button", { name: "生成中…", exact: true })).toBeDisabled();
  const { task_id: siblingId } = await submit<{ task_id: string }>(
    derivative(sheet, second).getByRole("button", { name: "生成衍生图" }),
    generateDerivative(HERO, second),
  );
  await expectRunningTogether(request, projectApi, [a.task_id, siblingId]);
  await expect(derivative(sheet, second).getByRole("button", { name: "生成中…", exact: true })).toBeDisabled();
  await release(request, [a.task_id, siblingId]);

  await expect(derivative(sheet, second).getByRole("button", { name: "生成衍生图" })).toBeEnabled();
  const base = await submit<{ task_id: string }>(sheet.getByRole("button", { name: "重新生成资产图" }), generateBase);
  await expect(derivative(sheet, second).getByRole("button", { name: "生成衍生图" })).toBeDisabled();
  await expectBusy(await request.post(generateDerivative(HERO.normalize("NFD"), first.normalize("NFD"))));

  await sheet.getByRole("button", { name: "可选：参考音频" }).click();
  await sheet.getByRole("button", { name: "用 TTS 生成参考音频" }).click();
  const voice = page.getByRole("dialog", { name: "生成语音参考样本" });
  await voice.getByRole("combobox", { name: "音色" }).fill("fixture-voice");
  await voice.getByRole("combobox", { name: "音色" }).press("Tab");
  const { task_id: voiceId } = await submit<{ task_id: string }>(
    voice.getByRole("button", { name: "生成", exact: true }), `${characterApi}/voice-sample`,
  );
  await expectRunningTogether(request, projectApi, [base.task_id, voiceId]);
  await expect(voice.getByRole("button", { name: "生成中…" })).toBeDisabled();

  await post(request, `/__playwright/history/${project}`);
  expect(await recentIds(request, project)).not.toContain(voiceId);
  await release(request, [voiceId]);
  await expect(voice.getByText("试听", { exact: true })).toBeVisible({ timeout: 15_000 });
  expect(await recentIds(request, project)).not.toContain(voiceId);
  await expectBusy(await request.post(generateDerivative(HERO, second)));
  await voice.getByRole("button", { name: "确认并保存" }).click();
  await expect(voice).toBeHidden();

  const response = await request.get(projectApi);
  await expect(response).toBeOK();
  expect(await response.json()).toMatchObject({
    project: { characters: { [HERO]: { reference_audio: `characters/refs_audio/${HERO}.wav` } } },
  });
  await release(request, [base.task_id]);
});

test("batch settlement and workflow leave the running state after task results fall off the recent page", async ({ page, request }) => {
  const project = "batch-workflow";
  const projectApi = await createProject(request, project, "批次与制作进度");
  await post(request, `${projectApi}/episodes`, { title: "第一集" });
  await addCharacter(request, projectApi, "角色甲");
  await addCharacter(request, projectApi, "角色乙");
  const { script_file } = await post<{ script_file: string }>(request, `${projectApi}/episodes/1/blank-script`, {});
  const { item } = await post<{ item: { segment_id: string } }>(request, `${projectApi}/script-items`, {
    script_file, novel_text: "角色甲遇见角色乙。",
  });
  await expect(await request.patch(`${projectApi}/segments/${item.segment_id}`, {
    data: { script_file, characters_in_segment: ["角色甲", "角色乙"] },
  })).toBeOK();

  await page.goto(`/app/projects/${project}/characters`);
  await page.getByRole("button", { name: "生成待生成的角色（2）" }).click();
  const batch = page.getByRole("dialog", { name: "生成待生成的资产图" });
  await expect(batch.getByText("将生成 2 张")).toBeVisible();
  const { members } = await submit<{ members: { task_id: string }[] }>(
    batch.getByRole("button", { name: "开始生成" }), `${projectApi}/asset-sheets/batch`,
  );
  expect(members).toHaveLength(2);
  const ids = members.map(({ task_id }) => task_id);
  await expect(batch).toBeHidden();

  await post(request, `/__playwright/history/${project}`);
  const before = await recentIds(request, project);
  for (const id of ids) expect(before).not.toContain(id);
  await page.getByRole("button", { name: /^1 第一集/ }).click();
  await page.getByTestId("workflow-panel").click();
  const workflow = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "制作进度", exact: true }) });
  await expect(workflow.getByText(/角色 · (生成中|排队中)/)).toHaveCount(2);

  await release(request, ids, "failure");
  await expect(workflow.getByText(/角色 · (生成中|排队中)/)).toHaveCount(0, { timeout: 15_000 });
  await expect(workflow).toBeVisible();
  const recent = await recentIds(request, project);
  for (const id of ids) expect(recent).not.toContain(id);

  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /^工作区通知/ }).click();
  const notifications = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "工作区通知" }) });
  await expect(notifications.getByText("资产图批量生成结束：0 张已生成，2 张失败")).toHaveCount(1);
  await expect(notifications.getByRole("listitem")).toHaveCount(1);
});
