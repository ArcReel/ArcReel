import "@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "@supabase/supabase-js";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

const PROVIDERS = [
  provider("gemini-aistudio", "Google AI Studio", true),
  provider("ark", "火山方舟", true),
  provider("ark-agent-plan", "火山方舟 Agent Plan", false),
  provider("grok", "xAI Grok", false),
  provider("openai", "OpenAI", true),
  provider("vidu", "Vidu", true),
  provider("dashscope", "阿里云百炼", true),
  provider("minimax", "MiniMax", true),
  {
    id: "kling",
    name: "可灵 Kling",
    secret_fields: [{ key: "api_key", label: "API Key" }, { key: "access_key", label: "Access Key" }, { key: "secret_key", label: "Secret Key" }],
    secret_field_groups: [["api_key"], ["access_key", "secret_key"]],
    supports_base_url: true,
  },
  provider("agnes", "Agnes", true),
  provider("runware", "Runware", true),
  provider("croco", "Croco GPU", true),
  provider("doubao", "火山 TTS", true),
];

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    authorize(req);
    const parts = new URL(req.url).pathname.split("/").filter(Boolean);
    const root = parts.indexOf("arcreel-admin");
    const path = root >= 0 ? parts.slice(root + 1) : parts;
    if (req.method === "GET" && path[0] === "schema") {
      return json({
        system_id: "arcreel",
        roles: [{ id: "admin", name: "管理员" }, { id: "user", name: "普通用户" }],
        providers: PROVIDERS,
      });
    }
    if (path[0] !== "accounts") return notFound();
    if (req.method === "GET" && path.length === 1) return await listAccounts(req);
    if (req.method === "POST" && path.length === 1) return await createAccount(req);
    const accountId = path[1];
    if (!accountId) return notFound();
    if (req.method === "PATCH" && path.length === 2) return await updateAccount(req, accountId);
    if (req.method === "POST" && path[2] === "reset-password") return await resetPassword(req, accountId);
    if (path[2] === "credentials") {
      if (req.method === "GET" && path.length === 3) return await listCredentials(accountId);
      const providerId = path[3];
      if (!providerId) return notFound();
      if (req.method === "PUT") return await putCredential(req, accountId, providerId);
      if (req.method === "DELETE") return await deleteCredential(accountId, providerId);
    }
    return notFound();
  } catch (error) {
    if (error instanceof HttpError) return json({ error: { code: error.code, message: error.message } }, error.status);
    console.error("arcreel-admin", error);
    return json({ error: { code: "INTERNAL_ERROR", message: "服务暂时不可用" } }, 500);
  }
});

async function listAccounts(req: Request) {
  const url = new URL(req.url);
  const page = Math.max(1, Number(url.searchParams.get("page") || 1));
  const pageSize = Math.min(100, Math.max(1, Number(url.searchParams.get("pageSize") || 20)));
  const search = (url.searchParams.get("search") || "").trim();
  let query = admin().from("arcreel_profiles")
    .select("id,username,display_name,role,status,created_at,updated_at", { count: "exact" });
  if (search) {
    const safe = search.replace(/[,%()]/g, "");
    query = query.or(`username.ilike.%${safe}%,display_name.ilike.%${safe}%`);
  }
  const { data, error, count } = await query.order("created_at", { ascending: false })
    .range((page - 1) * pageSize, page * pageSize - 1);
  if (error) throw error;
  return json({ items: data ?? [], total: count ?? 0, page, page_size: pageSize });
}

async function createAccount(req: Request) {
  const body = await readJson(req);
  const username = String(body.username ?? "").trim();
  const password = String(body.password ?? "");
  const displayName = optional(body.display_name);
  const role = validateRole(body.role);
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$/.test(username)) {
    throw new HttpError(400, "USERNAME_INVALID", "账号需为 2-64 位字母、数字、点、下划线或短横线");
  }
  if (password.length < 8) throw new HttpError(400, "PASSWORD_WEAK", "初始密码至少 8 位");
  const client = admin();
  const { data: duplicate } = await client.from("arcreel_profiles").select("id").ilike("username", username).maybeSingle();
  if (duplicate) throw new HttpError(409, "USERNAME_EXISTS", "账号已存在");
  const authEmail = `${username.toLowerCase()}@accounts.arcreel.invalid`;
  const { data: created, error: createError } = await client.auth.admin.createUser({
    email: authEmail,
    password,
    email_confirm: true,
    app_metadata: { application: "arcreel" },
  });
  if (createError || !created.user) {
    throw new HttpError(400, "ACCOUNT_CREATE_FAILED", createError?.message || "账号创建失败");
  }
  const { data, error } = await client.from("arcreel_profiles").insert({
    id: created.user.id,
    username,
    auth_email: authEmail,
    display_name: displayName,
    role,
    status: "active",
  }).select("id,username,display_name,role,status,created_at,updated_at").single();
  if (error) {
    await client.auth.admin.deleteUser(created.user.id);
    throw error;
  }
  return json({ account: data }, 201);
}

async function updateAccount(req: Request, accountId: string) {
  const body = await readJson(req);
  const patch: Record<string, unknown> = {};
  if ("display_name" in body) patch.display_name = optional(body.display_name);
  if ("role" in body) patch.role = validateRole(body.role);
  if ("status" in body) {
    const status = String(body.status);
    if (status !== "active" && status !== "disabled") throw new HttpError(400, "STATUS_INVALID", "账号状态无效");
    patch.status = status;
  }
  if (!Object.keys(patch).length) throw new HttpError(400, "NO_CHANGES", "没有需要保存的修改");
  const { data, error } = await admin().from("arcreel_profiles").update(patch).eq("id", accountId)
    .select("id,username,display_name,role,status,created_at,updated_at").maybeSingle();
  if (error) throw error;
  if (!data) throw new HttpError(404, "ACCOUNT_NOT_FOUND", "账号不存在");
  return json({ account: data });
}

async function resetPassword(req: Request, accountId: string) {
  const password = String((await readJson(req)).password ?? "");
  if (password.length < 8) throw new HttpError(400, "PASSWORD_WEAK", "新密码至少 8 位");
  const { error } = await admin().auth.admin.updateUserById(accountId, { password });
  if (error) throw new HttpError(400, "PASSWORD_RESET_FAILED", error.message);
  return json({ success: true });
}

async function listCredentials(accountId: string) {
  await requireAccount(accountId);
  const { data, error } = await admin().from("arcreel_provider_credentials")
    .select("provider_id,masked_hint,revision,updated_at").eq("user_id", accountId).order("provider_id");
  if (error) throw error;
  return json({ account_id: accountId, credentials: data ?? [] });
}

async function putCredential(req: Request, accountId: string, providerId: string) {
  await requireAccount(accountId);
  const provider = PROVIDERS.find((item) => item.id === providerId);
  if (!provider) throw new HttpError(400, "PROVIDER_INVALID", "ArcReel 不支持该供应商");
  const body = await readJson(req);
  const payload: Record<string, string> = { name: optional(body.name) || "数据中台分配" };
  for (const field of provider.secret_fields) {
    const value = optional(body[field.key]);
    if (value) payload[field.key] = value;
  }
  if (!provider.secret_field_groups.some((group) => group.every((key) => payload[key]))) {
    throw new HttpError(400, "CREDENTIAL_INCOMPLETE", "请完整填写一种可用的密钥组合");
  }
  const baseUrl = optional(body.base_url);
  if (baseUrl) payload.base_url = baseUrl;
  const encryptedPayload = await encryptPayload(payload);
  const maskedHint = Object.fromEntries(
    Object.entries(payload).filter(([key]) => key !== "name" && key !== "base_url")
      .map(([key, value]) => [key, mask(value)]),
  );
  const client = admin();
  const { data: existing } = await client.from("arcreel_provider_credentials").select("revision")
    .eq("user_id", accountId).eq("provider_id", providerId).maybeSingle();
  const revision = Number(existing?.revision || 0) + 1;
  const { data, error } = await client.from("arcreel_provider_credentials").upsert({
    user_id: accountId,
    provider_id: providerId,
    encrypted_payload: encryptedPayload,
    masked_hint: maskedHint,
    revision,
  }, { onConflict: "user_id,provider_id" }).select("provider_id,masked_hint,revision,updated_at").single();
  if (error) throw error;
  return json({ credential: data });
}

async function deleteCredential(accountId: string, providerId: string) {
  const { error } = await admin().from("arcreel_provider_credentials").delete()
    .eq("user_id", accountId).eq("provider_id", providerId);
  if (error) throw error;
  return new Response(null, { status: 204, headers: cors });
}

async function requireAccount(accountId: string) {
  const { data } = await admin().from("arcreel_profiles").select("id").eq("id", accountId).maybeSingle();
  if (!data) throw new HttpError(404, "ACCOUNT_NOT_FOUND", "账号不存在");
}

function authorize(req: Request) {
  const actual = req.headers.get("Authorization")?.replace(/^Bearer\s+/i, "").trim() ?? "";
  const expected = required("ARCREEL_ADMIN_INTEGRATION_TOKEN");
  if (!actual || !timingSafeEqual(actual, expected)) {
    throw new HttpError(401, "INTEGRATION_TOKEN_INVALID", "子系统管理凭证无效");
  }
}

function admin() {
  return createClient(required("SUPABASE_URL"), required("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function encryptPayload(payload: Record<string, string>): Promise<string> {
  const keyBytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(required("ARCREEL_CREDENTIAL_ENCRYPTION_KEY")));
  const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify(payload)));
  return `${toBase64(iv)}.${toBase64(new Uint8Array(encrypted))}`;
}

function toBase64(value: Uint8Array) {
  return btoa(String.fromCharCode(...value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function mask(value: string) { return value.length <= 6 ? "******" : `${value.slice(0, 3)}***${value.slice(-3)}`; }
function optional(value: unknown) { const text = String(value ?? "").trim(); return text || null; }
function validateRole(value: unknown): "admin" | "user" {
  const role = String(value || "user");
  if (role !== "admin" && role !== "user") throw new HttpError(400, "ROLE_INVALID", "角色无效");
  return role;
}
function timingSafeEqual(a: string, b: string) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return result === 0;
}
async function readJson(req: Request): Promise<Record<string, unknown>> {
  try { return await req.json(); } catch { throw new HttpError(400, "INVALID_JSON", "请求格式无效"); }
}
class HttpError extends Error { constructor(readonly status: number, readonly code: string, message: string) { super(message); } }
function required(name: string) { const value = Deno.env.get(name)?.trim(); if (!value) throw new Error(`${name} is required`); return value; }
function notFound() { return json({ error: { code: "NOT_FOUND", message: "接口不存在" } }, 404); }
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function provider(id: string, name: string, supportsBaseUrl: boolean) {
  return {
    id,
    name,
    secret_fields: [{ key: "api_key", label: "API Key" }],
    secret_field_groups: [["api_key"]],
    supports_base_url: supportsBaseUrl,
  };
}
