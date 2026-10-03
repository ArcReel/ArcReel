// 对真实后端录制页面级套件的接口数据替身：`pnpm e2e:record`。
// 在临时数据目录里启动后端、创建演示项目，按 RECORDINGS 逐个请求并写入 e2e/fixtures/recorded/。
// 时间戳、临时数据目录、仓库检出路径、令牌与项目修订号改写成固定值，重录后只有接口形状的变化会出现在 diff 里。
// 后端改动接口形状的 PR 同时重录。
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
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
  /** 替身文件名（不含扩展名）。 */
  file: string;
  method: "GET" | "POST";
  /** 页面实际请求的路径；带查询串时写全，参数顺序不限。查询串不同的请求分别录制。 */
  path: string;
  /** 不带令牌请求。 */
  anonymous?: boolean;
  form?: Record<string, string>;
  json?: unknown;
  /** 按录制环境（没有配置供应商）应当返回的非 2xx 状态，照实录下。 */
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
  { file: "auth-status", method: "GET", path: "/api/v1/auth/status", anonymous: true },
  LOGIN,
  { file: "system-config", method: "GET", path: "/api/v1/system/config" },
  { file: "providers", method: "GET", path: "/api/v1/providers" },
  { file: "custom-providers", method: "GET", path: "/api/v1/custom-providers" },
  { file: "onboarding-status", method: "GET", path: "/api/v1/onboarding/status" },
  { file: "projects", method: "GET", path: "/api/v1/projects" },
  { file: "project-demo", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}` },
  // 工作区与集页的启动请求（项目事件流是 SSE，不录制，场景里按需替换）。
  { file: "tasks", method: "GET", path: "/api/v1/tasks?page_size=200" },
  { file: "project-demo-tasks", method: "GET", path: `/api/v1/tasks?page_size=200&project_name=${DEMO_PROJECT}` },
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
];

// 每次录制都会变的不透明值：令牌按签发时刻生成，项目修订号是含创建时间的 project.json 摘要。
const FIXED_VALUES = new Map<string, string>([
  ["access_token", RECORDED_ACCESS_TOKEN],
  ["project_revision", "sha256-v1:<project-revision>"],
]);

const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/;

function normalize(value: unknown, dataDir: string): unknown {
  if (typeof value === "string") {
    if (ISO_TIMESTAMP.test(value)) return FIXED_NOW;
    return value.replaceAll(dataDir, "<data-dir>").replaceAll(REPO_ROOT, "<repo-root>");
  }
  if (Array.isArray(value)) return value.map((item) => normalize(item, dataDir));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, FIXED_VALUES.get(key) ?? normalize(item, dataDir)]),
    );
  }
  return value;
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
  const body: unknown = await resp.json();
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

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "arcreel-e2e-"));
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    ARCREEL_DATA_DIR: dataDir,
    AUTH_ENABLED: "true",
    AUTH_USERNAME: USERNAME,
    AUTH_PASSWORD: PASSWORD,
    AUTH_TOKEN_SECRET: "e2e-token-secret-for-recording-only",
  };
  // 数据库落在临时数据目录里，不碰开发者自己的库。
  delete env.DATABASE_URL;
  delete env.AI_ANIME_PROJECTS;

  const backend = spawn(
    "uv",
    ["run", "--frozen", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", String(port)],
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
    // 一集还没有分镜的空正式脚本，集页显示「新增第一个分镜」。
    await postJson(baseUrl, token, `/api/v1/projects/${DEMO_PROJECT}/episodes`, { title: "第一集" });
    await postJson(baseUrl, token, `/api/v1/projects/${DEMO_PROJECT}/episodes/1/blank-script`, {});

    rmSync(RECORDED_DIR, { recursive: true, force: true });
    mkdirSync(RECORDED_DIR, { recursive: true });
    for (const recording of RECORDINGS) {
      const { status, body } = await request(baseUrl, token, recording);
      const recorded: RecordedResponse = {
        method: recording.method,
        path: recording.path,
        status,
        body: normalize(body, dataDir),
      };
      writeFileSync(join(RECORDED_DIR, `${recording.file}.json`), `${JSON.stringify(recorded, null, 2)}\n`);
    }
    console.log(`已录制 ${readdirSync(RECORDED_DIR).length} 个接口到 ${RECORDED_DIR}`);
  } finally {
    if (!exited) backend.kill("SIGTERM");
    await backendExit;
    rmSync(dataDir, { recursive: true, force: true });
  }
}

await main();
