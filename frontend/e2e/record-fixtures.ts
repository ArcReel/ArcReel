// Record page-suite API fixtures against real backends: `pnpm e2e:record`.
// Start backends in temporary data directories, create demos, request RECORDINGS sequentially and write e2e/fixtures/recorded/.
// Normalize timestamps/temp roots/checkout paths/tokens/project revisions so rerecord diffs show interface-shape changes only.
// Backend API-shape PRs rerecord fixtures too.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
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
  /** Fixture filenames without extensions. */
  file: string;
  method: "GET" | "POST" | "DELETE";
  /** Actual request paths, including complete queries in any parameter order; distinct queries record separately. */
  path: string;
  /** Requests without tokens. */
  anonymous?: boolean;
  form?: Record<string, string>;
  json?: unknown;
  /** Expected non-2xx statuses in provider-unconfigured recording environments, recorded truthfully. */
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
  { file: "custom-providers-endpoints", method: "GET", path: "/api/v1/custom-providers/endpoints" },
  { file: "onboarding-status", method: "GET", path: "/api/v1/onboarding/status" },
  { file: "projects", method: "GET", path: "/api/v1/projects" },
  { file: "deleted-projects", method: "GET", path: "/api/v1/deleted-projects" },
  { file: "project-demo", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}` },
  // Workspace/episode startup requests (project event SSE streams are unrecorded, replaced by scenarios as needed).
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
  // Global settings: default provider details (first list item) and default-model candidates.
  { file: "provider-gemini-aistudio-config", method: "GET", path: "/api/v1/providers/gemini-aistudio/config" },
  { file: "provider-gemini-aistudio-credentials", method: "GET", path: "/api/v1/providers/gemini-aistudio/credentials" },
  { file: "provider-gemini-aistudio-multiplier", method: "GET", path: "/api/v1/providers/gemini-aistudio/multiplier" },
  { file: "provider-gemini-aistudio-models", method: "GET", path: "/api/v1/providers/gemini-aistudio/models" },
  { file: "system-config-model-candidates", method: "GET", path: "/api/v1/system/config/model-candidates" },
  // Actual models for Automatic default-model selection (recorded environments lack providers, so no uses resolve), shared by global/project settings and new-project wizards.
  { file: "system-config-auto-backends", method: "GET", path: "/api/v1/system/config/auto-backends" },
  // Global settings: access-token lists (recorded environments lack tokens; scenarios replace multiple entries).
  { file: "api-keys", method: "GET", path: "/api/v1/api-keys" },
  // Global ArcReel Agent settings: Agent-provider lists (empty in recordings) and add-dialog preset-provider catalogs.
  { file: "agent-credentials", method: "GET", path: "/api/v1/agent/credentials" },
  { file: "agent-preset-providers", method: "GET", path: "/api/v1/agent/preset-providers" },
  // Global Agent memory settings: user-memory directories (recorded environments lack memory files; scenarios replace entries/bodies).
  { file: "agent-memory", method: "GET", path: "/api/v1/agent/memory" },
  // Global Usage settings: default past 30 days (start derived from fixed times/Asia/Shanghai) and running calls.
  { file: "usage-summary-30d", method: "GET", path: "/api/v1/usage/summary?since=2025-12-02T16:00:00.000Z&tz=Asia/Shanghai" },
  {
    file: "usage-records-30d",
    method: "GET",
    path: "/api/v1/usage/records?limit=20&since=2025-12-02T16:00:00.000Z&status=success,failed,cancelled",
  },
  { file: "usage-records-pending", method: "GET", path: "/api/v1/usage/records?limit=20&status=pending" },
  // Global settings: About (external version information replaced by scenarios) and prompt-template lists/details.
  { file: "official-service", method: "GET", path: "/api/v1/official-service" },
  { file: "prompt-templates", method: "GET", path: "/api/v1/prompt-templates" },
  { file: "prompt-template-episode-plan", method: "GET", path: "/api/v1/prompt-templates/text/episode_plan" },
  {
    file: "prompt-partial-additional-instructions",
    method: "GET",
    path: "/api/v1/prompt-templates/partials/shared/additional_instructions",
  },
  // Global API endpoints settings: custom endpoint lists (empty in recordings; scenarios supply entries) and share submissions determining share entries; official-service toggles above.
  { file: "custom-endpoints", method: "GET", path: "/api/v1/custom-endpoints" },
  // Read-only built-in endpoint definitions.
  {
    file: "custom-providers-endpoint-newapi-video-definition",
    method: "GET",
    path: "/api/v1/custom-providers/endpoints/newapi-video/definition",
  },
  { file: "market-submissions", method: "GET", path: "/api/v1/market/submissions" },
  // Global Material libraries/storage settings: object-storage connections/new-image save locations (no connections in recordings; local saves), library accounts/inference credential bindings (empty); scenarios supply configured states.
  { file: "object-storage-connection", method: "GET", path: "/api/v1/object-storage/connection" },
  { file: "asset-library-accounts", method: "GET", path: "/api/v1/asset-library/accounts" },
  { file: "asset-library-bindings", method: "GET", path: "/api/v1/asset-library/bindings" },
  // Global Market settings: sources (only never-refreshed built-in official sources in recordings) and empty entry snapshots; scenarios replace external refresh/aggregation/details.
  { file: "market-sources", method: "GET", path: "/api/v1/market/sources" },
  { file: "market-entries", method: "GET", path: "/api/v1/market/entries?type=endpoint" },
  // New-project wizards: TTS prefilled values (global defaults).
  { file: "narration-defaults", method: "GET", path: "/api/v1/system/narration-defaults" },
  // Asset libraries: default Characters first pages (same request as gallery Import from library), gallery asset-image states and name-duplicate checks for add previews.
  { file: "assets-character", method: "GET", path: "/api/v1/assets?limit=60&type=character" },
  { file: "project-demo-asset-sheets-status", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/asset-sheets/status` },
  { file: "assets-character-search-lin-xi", method: "GET", path: `/api/v1/assets?q=${encodeURIComponent("林夕")}&type=character` },
  // Project settings: Agent configuration states/project memory (demos lack custom configurations/memory files; scenarios replace entries).
  { file: "project-demo-agent-profile", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/agent-profile` },
  { file: "project-demo-agent-memory", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/agent-memory` },
  // Project Episodes views: demos lack whole sources and contain only one sourceless episode; scenarios replace whole sources/multiple episodes/unregistered files.
  { file: "project-demo-episodes-view", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/episodes-view` },
  // Project Files views: project roots/scripts directories (demos contain only one empty formal episode script; scenarios replace multiple files/long filenames/preview bodies).
  { file: "project-demo-file-view", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/file-view?path=` },
  { file: "project-demo-file-view-scripts", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/file-view?path=scripts` },
  // File-view Project trash sections: demos lack deletion records; scenarios supply multiple records.
  { file: "project-demo-trash", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/trash` },
  // Episode Multi-grid storyboard images: grid-tier limits/records (demos lack grids; scenarios supply grouped composites).
  { file: "project-demo-grid-capability", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/grid-capability` },
  { file: "project-demo-grids", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/grids` },
  // Episode editing views: demos lack videos and therefore editing timelines, showing empty states; scenarios replace timeline reads/export states.
  { file: "project-demo-edit-timelines", method: "GET", path: `/api/v1/projects/${DEMO_PROJECT}/edit-timelines?episode=1` },
];

// Demo library assets: two characters, one scene and one prop, all without images.
const LIBRARY_ASSETS: Record<string, string>[] = [
  { type: "character", name: "林夕", description: "二十出头的旧城茶馆老板娘，短发，常穿靛蓝布衫。", voice_style: "温和、语速偏慢" },
  { type: "character", name: "陈默", description: "沉默寡言的修表匠，左手腕上有一道旧疤。", voice_style: "" },
  { type: "scene", name: "旧城茶馆", description: "临街的两层木楼，一楼摆着八张方桌。", voice_style: "" },
  { type: "prop", name: "青铜罗盘", description: "巴掌大的罗盘，指针总是指向茶馆。", voice_style: "" },
];

// Opaque values changing each recording: tokens derive from issuance times; project revisions digest project.json including creation times.
const FIXED_VALUES = new Map<string, string>([
  ["access_token", RECORDED_ACCESS_TOKEN],
  ["project_revision", "sha256-v1:<project-revision>"],
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// Identical UUIDs within recordings map to identical fixed values, numbered in first-appearance order.
const uuidAliases = new Map<string, string>();

function fixedUuid(value: string): string {
  let alias = uuidAliases.get(value);
  if (!alias) {
    alias = `00000000-0000-4000-8000-${String(uuidAliases.size + 1).padStart(12, "0")}`;
    uuidAliases.set(value, alias);
  }
  return alias;
}

const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/;

function normalize(value: unknown, dataDir: string): unknown {
  if (typeof value === "string") {
    if (ISO_TIMESTAMP.test(value)) return FIXED_NOW;
    if (UUID.test(value)) return fixedUuid(value);
    return value.replaceAll(dataDir, "<data-dir>").replaceAll(REPO_ROOT, "<repo-root>")
      .replace(/#deleted-\d{8}T\d{6}Z$/, "#deleted-20260101T080000Z");
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
      // Backends have not started listening yet.
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

async function postForm(baseUrl: string, token: string, path: string, fields: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  const resp = await fetch(`${baseUrl}${path}`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form });
  if (!resp.ok) throw new Error(`POST ${path} 返回 ${resp.status}：${await resp.text()}`);
}

async function main() {
  // Resolve real paths: macOS temporary directories use /var → /private/var symlinks; backends return resolved paths.
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
    // Backend startup reads .env, including parent main-checkout .env, whose DATABASE_URL would connect recording to developer databases.
    PYTHON_DOTENV_DISABLED: "1",
  };
  // Databases reside in temporary roots, never touching developer databases.
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
    // Empty formal scripts without episode storyboards show Add first storyboard.
    await postJson(baseUrl, token, `/api/v1/projects/${DEMO_PROJECT}/episodes`, { title: "第一集" });
    await postJson(baseUrl, token, `/api/v1/projects/${DEMO_PROJECT}/episodes/1/blank-script`, {});
    await postJson(baseUrl, token, "/api/v1/projects", { name: "deleted-demo", title: "已删除演示项目", generation_mode: "storyboard" });
    await request(baseUrl, token, { method: "DELETE", path: "/api/v1/projects/deleted-demo" });
    // Libraries list newest-updated first; sequential creation reverses LIBRARY_ASSETS order consistently across recordings.
    for (const asset of LIBRARY_ASSETS) await postForm(baseUrl, token, "/api/v1/assets", asset);

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
