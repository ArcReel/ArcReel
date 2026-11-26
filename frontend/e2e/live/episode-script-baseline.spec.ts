import { expect, test, type APIRequestContext } from "@playwright/test";

function latch() {
  let open!: () => void;
  const wait = new Promise<void>((resolve) => { open = resolve; });
  return { wait, open };
}

async function write(request: APIRequestContext, path: string, data?: unknown) {
  const result = await request.post(path, { data });
  expect(result.ok(), await result.text()).toBe(true);
}

for (const initial of ["ready", "missing"] as const) {
  test(`first subscription reconciles a previously ${initial} script after another session writes`, async ({ page, request }) => {
    const name = `live-initial-script-${initial}`;
    const project = `/api/v1/projects/${name}`;
    const script = `${project}/scripts/episode_1.json`;
    await write(request, "/api/v1/onboarding/seen", {});
    await write(request, "/api/v1/projects", { name, title: name, content_mode: "narration", generation_mode: "storyboard" });
    await write(request, `${project}/episodes`, { title: "第 1 集", source_text: "雨停了。" });
    const createScript = async () => {
      await write(request, `${project}/episodes/1/blank-script`);
      await write(request, `${project}/script-items`, { script_file: "episode_1.json", novel_text: "雨停了。" });
    };
    const setPrompt = async (prompt: string) => {
      const snapshot = await (await request.get(script)).json() as { revision: string; script: { segments: { segment_id: string }[] } };
      await write(request, `${project}/script-edits`, {
        episode: 1, expected_revision: snapshot.revision,
        operations: [{ op: "update", id: snapshot.script.segments[0].segment_id, fields: { image_prompt: prompt } }],
      });
    };
    if (initial === "ready") {
      await createScript();
      await setPrompt("旧提示词");
    }

    const firstRead = latch();
    const workspaceStarted = latch();
    const subscribe = latch();
    const releaseWorkspace = latch();
    const releaseScript = latch();
    let scriptReads = 0;
    await page.route(`**${script}`, async (route) => {
      if (++scriptReads > 1) return route.continue();
      const response = await route.fetch();
      firstRead.open();
      await releaseScript.wait;
      await route.fulfill({ response });
    });
    await page.route(`**${project}/workspace`, async (route) => {
      workspaceStarted.open();
      await releaseWorkspace.wait;
      await route.continue();
    });
    await page.route(`**${project}/events/stream*`, async (route) => {
      await subscribe.wait;
      await route.continue();
    });
    await page.goto(`/app/projects/${name}/episodes/1`, { waitUntil: "domcontentloaded" });
    try {
      await Promise.all([firstRead.wait, workspaceStarted.wait]);
      if (initial === "missing") await createScript();
      await setPrompt("新提示词");
      if (initial === "ready") releaseScript.open();
      const loadedWorkspace = page.waitForResponse((response) => new URL(response.url()).pathname === `${project}/workspace`);
      releaseWorkspace.open();
      await loadedWorkspace;
      await expect(page.getByRole("button", { name: /^生成分镜(?:\s|$)/ })).toHaveCount(0);
      const opened = page.waitForResponse((response) => response.url().includes(`${project}/events/stream`));
      subscribe.open();
      await opened;
      releaseScript.open();
      await expect(page.getByRole("textbox", { name: "分镜图提示词" })).toHaveValue("新提示词");
      expect(scriptReads).toBe(2);
      const queued = page.waitForResponse((response) => response.request().method() === "POST" && response.url().includes("/generate/storyboard/"));
      await page.getByRole("button", { name: /^生成分镜(?:\s|$)/ }).click();
      const submission = await queued;
      expect(submission.request().postDataJSON()).toMatchObject({ prompt: "新提示词" });
      expect(submission.ok(), await submission.text()).toBe(true);
      const { task_id } = await submission.json() as { task_id: string };
      expect(task_id).toBeTruthy();
      await write(request, "/__playwright/release", { task_ids: [task_id], outcome: "success" });
    } finally {
      subscribe.open();
      releaseWorkspace.open();
      releaseScript.open();
    }
  });
}
