// `pnpm e2e:record:check`：再起一个换掉系统时钟、出厂快照与版本号真实来源的后端，两边各录一次，替身不同即失败，不写文件。
import { COMFYUI_AUDIO_ASPECT } from "./fixtures/comfyui.ts";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  FIXED_NOW,
  RECORDED_ACCESS_TOKEN,
  RECORDED_DIR,
  type RecordedResponse,
} from "./support/recorded.ts";

const REPO_ROOT = resolve(import.meta.dirname, "..", "..");
const USERNAME = "e2e";
const PASSWORD = "e2e-password";
const DEMO_PROJECT = "demo";

interface Recording {
  file: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  anonymous?: boolean;
  form?: Record<string, string>;
  json?: unknown;
  status?: number;
}

const LOGIN: Recording = {
  file: "auth-token",
  method: "POST",
  path: "/api/v1/auth/token",
  anonymous: true,
  form: { username: USERNAME, password: PASSWORD, grant_type: "password" },
};

const RECORDINGS: Recording[] = [
  { file: "comfyui-audio-aspect-infer", method: "POST", path: "/api/v1/custom-endpoints/comfyui/infer", json: COMFYUI_AUDIO_ASPECT },
  { file: "auth-status", method: "GET", path: "/api/v1/auth/status", anonymous: true },
  LOGIN,
  { file: "system-config", method: "GET", path: "/api/v1/system/config" },
  { file: "providers", method: "GET", path: "/api/v1/providers" },
  { file: "custom-providers", method: "GET", path: "/api/v1/custom-providers" },
  { file: "custom-providers-endpoints", method: "GET", path: "/api/v1/custom-providers/endpoints" },
  { file: "onboarding-status", method: "GET", path: "/api/v1/onboarding/status" },
  { file: "projects", method: "GET", path: "/api/v1/projects" },
  { file: "deleted-projects", method: "GET", path: "/api/v1/deleted-projects" },
  { file: "project-demo-workspace", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/workspace` },
  { file: "project-demo-workspace-episode-1", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/workspace?episodes=1` },
  { file: "project-demo-script-episode-1", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/scripts/episode_1.json` },
  { file: "project-demo-item-locations", method: "POST", path: `/api/v1/projects/${DEMO_PROJECT}/item-locations`, json: { item_ids: [] } },
  // 工作区与集页的启动请求（项目事件流是 SSE，不录制，场景里按需替换）。
  { file: "tasks", method: "GET", path: "/api/v1/tasks?page_size=200" },
  { file: "project-demo-tasks", method: "GET", path: `/api/v1/tasks?page_size=200&project_name=${DEMO_PROJECT}` },
  { file: "tasks-active", method: "GET", path: "/api/v1/tasks/active" },
  { file: "project-demo-tasks-active", method: "GET", path: `/api/v1/tasks/active?project_name=${DEMO_PROJECT}` },
  { file: "tasks-lookup", method: "POST", path: "/api/v1/tasks/lookup", json: { task_ids: [] } },
  { file: "tasks-stats", method: "GET", path: "/api/v1/tasks/stats" },
  { file: "project-demo-tasks-stats", method: "GET", path: `/api/v1/tasks/stats?project_name=${DEMO_PROJECT}` },
  { file: "project-demo-usage-summary", method: "GET", path: `/api/v1/usage/summary?project_name=${DEMO_PROJECT}` },
  {
    file: "project-demo-usage-records-recent",
    method: "GET",
    path: `/api/v1/usage/records?limit=10&project_name=${DEMO_PROJECT}&status=success,failed,cancelled`,
  },
  { file: "project-demo-usage-records-pending", method: "GET", path: `/api/v1/usage/records?project_name=${DEMO_PROJECT}&status=pending` },
  { file: "project-demo-video-capabilities", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/video-capabilities`, status: 422 },
  { file: "project-demo-assistant-skills", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/assistant/skills` },
  { file: "project-demo-assistant-sessions", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/assistant/sessions` },
  { file: "project-demo-workflow-status", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/workflow-status` },
  { file: "project-demo-workflow-plan", method: "POST", path: `/api/v1/projects/${DEMO_PROJECT}/workflow-plan`, json: { episode_id: 1 } },
  { file: "project-demo-cost-estimate", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/cost-estimate` },
  { file: "provider-gemini-aistudio-config", method: "GET", path: "/api/v1/providers/gemini-aistudio/config" },
  { file: "provider-gemini-aistudio-credentials", method: "GET", path: "/api/v1/providers/gemini-aistudio/credentials" },
  { file: "provider-gemini-aistudio-multiplier", method: "GET", path: "/api/v1/providers/gemini-aistudio/multiplier" },
  { file: "provider-gemini-aistudio-models", method: "GET", path: "/api/v1/providers/gemini-aistudio/models" },
  { file: "system-config-model-candidates", method: "GET", path: "/api/v1/system/config/model-candidates" },
  { file: "system-config-auto-backends", method: "GET", path: "/api/v1/system/config/auto-backends" },
  { file: "system-config-recommendation-changes", method: "GET", path: "/api/v1/system/config/recommendation-changes" },
  { file: "api-keys", method: "GET", path: "/api/v1/api-keys" },
  { file: "agent-credentials", method: "GET", path: "/api/v1/agent/credentials" },
  { file: "agent-preset-providers", method: "GET", path: "/api/v1/agent/preset-providers" },
  { file: "agent-memory", method: "GET", path: "/api/v1/agent/memory" },
  // 全局设置「使用记录」：默认最近 30 天（起点由固定时间与 Asia/Shanghai 推出）与进行中的调用。
  { file: "usage-summary-30d", method: "GET", path: "/api/v1/usage/summary?since=2025-12-02T16:00:00.000Z&tz=Asia/Shanghai" },
  {
    file: "usage-records-30d",
    method: "GET",
    path: "/api/v1/usage/records?limit=20&since=2025-12-02T16:00:00.000Z&status=success,failed,cancelled",
  },
  { file: "usage-records-pending", method: "GET", path: "/api/v1/usage/records?limit=20&status=pending" },
  { file: "official-service", method: "GET", path: "/api/v1/official-service" },
  { file: "prompt-templates", method: "GET", path: "/api/v1/prompt-templates" },
  { file: "prompt-template-episode-plan", method: "GET", path: "/api/v1/prompt-templates/text/episode_plan" },
  {
    file: "prompt-partial-additional-instructions",
    method: "GET",
    path: "/api/v1/prompt-templates/partials/shared/additional_instructions",
  },
  { file: "custom-endpoints", method: "GET", path: "/api/v1/custom-endpoints" },
  {
    file: "custom-providers-endpoint-newapi-video-definition",
    method: "GET",
    path: "/api/v1/custom-providers/endpoints/newapi-video/definition",
  },
  { file: "market-submissions", method: "GET", path: "/api/v1/market/submissions" },
  { file: "object-storage-connection", method: "GET", path: "/api/v1/object-storage/connection" },
  { file: "asset-library-accounts", method: "GET", path: "/api/v1/asset-library/accounts" },
  { file: "asset-library-bindings", method: "GET", path: "/api/v1/asset-library/bindings" },
  { file: "market-sources", method: "GET", path: "/api/v1/market/sources" },
  { file: "market-entries", method: "GET", path: "/api/v1/market/entries?type=endpoint" },
  { file: "global-narration-settings", method: "GET", path: "/api/v1/system/global-narration-settings" },
  { file: "project-demo-audio-backend-voices", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/audio-backend/voices` },
  { file: "assets-character", method: "GET", path: "/api/v1/assets?limit=60&type=character" },
  { file: "project-demo-asset-sheets-status", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/asset-sheets/status` },
  {
    file: "project-demo-asset-sheets-status-derivative-refs",
    method: "GET",
    path: `/api/v1/projects/${DEMO_PROJECT}/asset-sheets/status?include=derivative_references`,
  },
  { file: "assets-character-by-name-lin-xi", method: "GET", path: `/api/v1/assets/by-name?type=character&name=${encodeURIComponent("林夕")}` },
  { file: "project-demo-agent-profile", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/agent-profile` },
  { file: "project-demo-agent-memory", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/agent-memory` },
  { file: "project-demo-episodes-view", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/episodes-view` },
  { file: "project-demo-file-view", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/file-view?path=` },
  { file: "project-demo-file-view-scripts", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/file-view?path=scripts` },
  { file: "project-demo-trash", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/trash` },
  { file: "project-demo-episode-grid-capability", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/grid-capability?episode=1&script_file=episode_1.json` },
  { file: "project-demo-grids", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/grids` },
  { file: "project-demo-edit-timelines", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/edit-timelines?episode=1` },
  { file: "comfyui-audio-aspect-create", method: "POST", path: "/api/v1/custom-endpoints", json: COMFYUI_AUDIO_ASPECT },
];

const LIBRARY_ASSETS: Record<string, string>[] = [
  { type: "character", name: "林夕", description: "二十出头的旧城茶馆老板娘，短发，常穿靛蓝布衫。", voice_style: "温和、语速偏慢" },
  { type: "character", name: "陈默", description: "沉默寡言的修表匠，左手腕上有一道旧疤。", voice_style: "" },
  { type: "scene", name: "旧城茶馆", description: "临街的两层木楼，一楼摆着八张方桌。", voice_style: "" },
  { type: "prop", name: "青铜罗盘", description: "巴掌大的罗盘，指针总是指向茶馆。", voice_style: "" },
];

// 每次录制都会变的不透明值：令牌按签发时刻生成，项目修订号是含创建时间的 project.json 摘要。
const FIXED_VALUES = new Map<string, string>([
  ["access_token", RECORDED_ACCESS_TOKEN],
  ["project_revision", "sha256-v1:<project-revision>"],
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/;
// 文件与目录的字节数随内嵌 Agent 配置（项目里的 .claude/ 与 CLAUDE.md）和项目文件内容变化，与接口形状无关。
// 非零值改写成同一个固定值，0（空目录、空回收站）照实保留。
const SIZE_KEYS = new Set(["size", "total_size"]);
const FIXED_SIZE = 4096;

function createNormalizer(dataDir: string): (value: unknown) => unknown {
  const uuidAliases = new Map<string, string>();
  const fixedUuid = (value: string): string => {
    let alias = uuidAliases.get(value);
    if (!alias) {
      alias = `00000000-0000-4000-8000-${String(uuidAliases.size + 1).padStart(12, "0")}`;
      uuidAliases.set(value, alias);
    }
    return alias;
  };
  const normalize = (value: unknown, parentKey?: string): unknown => {
    if (typeof value === "string") {
      if (ISO_TIMESTAMP.test(value)) return FIXED_NOW;
      if ((parentKey === "task_id" || parentKey === "batch_id") && /^[0-9a-f]{32}$/.test(value)) return fixedUuid(value).replaceAll("-", "");
      if (UUID.test(value)) return fixedUuid(value);
      return value.replaceAll(dataDir, "<data-dir>").replaceAll(REPO_ROOT, "<repo-root>")
        .replace(/#deleted-\d{8}T\d{6}Z$/, "#deleted-20260101T080000Z");
    }
    if (Array.isArray(value)) return value.map((item) => normalize(item));
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value).map(([key, item]) => {
          if (parentKey === undefined && key === "revision" && typeof item === "string" && item.startsWith("sha256-v1:")) {
            return [key, "sha256-v1:<script-revision>"];
          }
          const fixed = FIXED_VALUES.get(key);
          if (fixed !== undefined) return [key, fixed];
          if (SIZE_KEYS.has(key) && typeof item === "number" && item > 0) return [key, FIXED_SIZE];
          return [key, normalize(item, key)];
        }),
      );
    }
    return value;
  };
  return normalize;
}

function freePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (address && typeof address === "object") resolvePort(address.port);
        else reject(new Error("无法分配端口"));
      });
    });
  });
}

async function waitForHealth(baseUrl: string, backendExited: () => boolean) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (backendExited()) throw new Error("后端进程提前退出，见上方输出");
    try {
      const resp = await fetch(`${baseUrl}/health`);
      if (resp.ok) return;
    } catch {
      // 后端尚未开始监听。
    }
    await new Promise((done) => setTimeout(done, 500));
  }
  throw new Error("等待后端 /health 超时");
}

async function request(baseUrl: string, token: string | null, recording: Omit<Recording, "file">) {
  const headers: Record<string, string> = { "Accept-Language": "zh" };
  if (token && !recording.anonymous) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (recording.form) payload = new URLSearchParams(recording.form);
  else if (recording.json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(recording.json);
  }
  const resp = await fetch(`${baseUrl}${recording.path}`, { method: recording.method, headers, body: payload });
  const body: unknown = resp.status === 204 ? null : await resp.json();
  if (!resp.ok && resp.status !== recording.status) {
    throw new Error(`${recording.method} ${recording.path} 返回 ${resp.status}：${JSON.stringify(body)}`);
  }
  return { status: resp.status, body };
}

async function postJson(baseUrl: string, token: string, path: string, payload: unknown) {
  const resp = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`POST ${path} 返回 ${resp.status}：${await resp.text()}`);
}

async function postForm(baseUrl: string, token: string, path: string, fields: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  const resp = await fetch(`${baseUrl}${path}`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form });
  if (!resp.ok) throw new Error(`POST ${path} 返回 ${resp.status}：${await resp.text()}`);
}

async function record(perturb: boolean): Promise<Map<string, string>> {
  // 取真实路径：macOS 的临时目录经 /var → /private/var 符号链接，后端返回的是解析后的路径
  const dataDir = realpathSync(mkdtempSync(join(tmpdir(), "arcreel-e2e-")));
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    ARCREEL_DATA_DIR: dataDir,
    ARCREEL_SKIP_CATALOG_SYNC: "1",
    AUTH_ENABLED: "true",
    AUTH_USERNAME: USERNAME,
    AUTH_PASSWORD: PASSWORD,
    AUTH_TOKEN_SECRET: "e2e-token-secret-for-recording-only",
    // 后端启动时会读 .env（含向上查找到的主仓 .env），里面的 DATABASE_URL 会把录制接到开发者的库。
    PYTHON_DOTENV_DISABLED: "1",
  };
  delete env.DATABASE_URL;
  delete env.AI_ANIME_PROJECTS;

  const backend = spawn(
    "uv",
    [
      "run", "--frozen", "python", "scripts/e2e_record_backend.py",
      "--host", "127.0.0.1", "--port", String(port), ...(perturb ? ["--perturb"] : []),
    ],
    { cwd: REPO_ROOT, env, stdio: ["ignore", "inherit", "inherit"] },
  );
  let exited = false;
  const backendExit = new Promise<void>((done) => {
    backend.once("exit", () => {
      exited = true;
      done();
    });
  });

  try {
    await waitForHealth(baseUrl, () => exited);
    const login = await request(baseUrl, null, LOGIN);
    const token = (login.body as { access_token: string }).access_token;

    await postJson(baseUrl, token, "/api/v1/onboarding/seen", {});
    await postJson(baseUrl, token, "/api/v1/projects", { name: DEMO_PROJECT, title: "演示项目", generation_mode: "storyboard" });
    await postJson(baseUrl, token, `/api/v1/projects/${DEMO_PROJECT}/episodes`, { title: "第一集" });
    await postJson(baseUrl, token, `/api/v1/projects/${DEMO_PROJECT}/episodes/1/blank-script`, {});
    await postJson(baseUrl, token, "/api/v1/projects", { name: "deleted-demo", title: "已删除演示项目", generation_mode: "storyboard" });
    await request(baseUrl, token, { method: "DELETE", path: "/api/v1/projects/deleted-demo" });
    for (const asset of LIBRARY_ASSETS) await postForm(baseUrl, token, "/api/v1/assets", asset);

    const normalize = createNormalizer(dataDir);
    const files = new Map<string, string>();
    async function recordResponse(recording: Recording) {
      const { status, body } = await request(baseUrl, token, recording);
      const recorded: RecordedResponse = {
        method: recording.method,
        path: recording.path,
        status,
        body: normalize(body),
      };
      files.set(`${recording.file}.json`, `${JSON.stringify(recorded, null, 2)}\n`);
    }
    for (const recording of RECORDINGS) await recordResponse(recording);
    await postJson(baseUrl, token, "/api/v1/projects", { name: "progress-demo", title: "文本进展", generation_mode: "storyboard" });
    await postJson(baseUrl, token, "/__record/start-progress", {});
    await recordResponse({ file: "running-text-task", method: "GET", path: "/api/v1/projects/progress-demo/tasks/active" });
    await postJson(baseUrl, token, "/__record/finish-progress", {});
    const planningProject = "/api/v1/projects/progress-demo";
    await postJson(baseUrl, token, `${planningProject}/episodes`, { title: "长文本规划" });
    const episode = `${planningProject}/episodes/1`;
    await recordResponse({ file: "episode-source-save", method: "PUT", path: `${episode}/source`, json: { text: "山雨初歇。", confirm: false } });
    await recordResponse({ file: "episode-script-plan", method: "POST", path: `${episode}/script-plan`, json: {} });
    return files;
  } finally {
    if (!exited) backend.kill("SIGTERM");
    await backendExit;
    rmSync(dataDir, { recursive: true, force: true });
  }
}

function describeDrift(baseline: Map<string, string>, perturbed: Map<string, string>): string[] {
  const report: string[] = [];
  for (const file of new Set([...baseline.keys(), ...perturbed.keys()])) {
    const expected = baseline.get(file);
    const actual = perturbed.get(file);
    if (expected === undefined || actual === undefined) {
      report.push(`${file}：${expected === undefined ? "仅出现在第二份录制" : "仅出现在第一份录制"}`);
      continue;
    }
    if (actual === expected) continue;
    const left = expected.split("\n");
    const right = actual.split("\n");
    let line = 0;
    while (line < left.length && left[line] === right[line]) line += 1;
    const from = Math.max(0, line - 3);
    report.push(
      `${file}：第 ${line + 1} 行起不同`,
      ...left.slice(from, line + 3).map((text) => `  - ${text}`),
      ...right.slice(from, line + 3).map((text) => `  + ${text}`),
    );
  }
  return report;
}

function readFiles(dir: string): Map<string, string> {
  return new Map(readdirSync(dir).map((name) => [name, readFileSync(join(dir, name), "utf8")]));
}

function writeFiles(dir: string, files: Map<string, string>) {
  for (const [file, content] of files) writeFileSync(join(dir, file), content);
}

async function main() {
  if (process.argv.includes("--check")) {
    // 两边都收尾（停掉后端、删掉数据目录）后再报错，免得一边失败时另一边的后端成为孤儿进程。
    const settled = await Promise.allSettled([record(false), record(true)]);
    const failure = settled.find((result) => result.status === "rejected");
    if (failure) throw failure.reason;
    const [baseline, perturbed] = settled.map((result) => (result as PromiseFulfilledResult<Map<string, string>>).value);
    const drift = describeDrift(baseline, perturbed);
    if (drift.length > 0) {
      console.error("替身随系统时钟、出厂快照或版本号变化（- 为原样录制，+ 为换掉这些来源后录制）：");
      for (const line of drift) console.error(line);
      process.exitCode = 1;
      return;
    }
    console.log(`换掉系统时钟、出厂快照与版本号后录出的 ${baseline.size} 个替身不变`);
    return;
  }
  const files = await record(false);
  if (process.argv.includes("--freshness")) {
    const dir = mkdtempSync(join(tmpdir(), "arcreel-recordings-"));
    try {
      writeFiles(dir, files);
      const drift = describeDrift(readFiles(RECORDED_DIR), readFiles(dir));
      if (drift.length) throw new Error(`录制与当前后端不一致（第一份为已提交录制）：\n${drift.join("\n")}`);
      console.log(`已核对 ${files.size} 份录制的文件集合与内容`);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
    return;
  }
  rmSync(RECORDED_DIR, { recursive: true, force: true });
  mkdirSync(RECORDED_DIR, { recursive: true });
  writeFiles(RECORDED_DIR, files);
  console.log(`已录制 ${files.size} 个接口到 ${RECORDED_DIR}`);
}

await main();
