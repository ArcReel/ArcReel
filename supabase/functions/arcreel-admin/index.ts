import "@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "@supabase/supabase-js";
import CONFIG_SCHEMA_JSON from "../_shared/arcreel-config-schema.json" with { type: "json" };

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

type ProviderField = { key: string; label: string; type: "text" | "url" | "number"; required: boolean };
type ProviderDefinition = {
  id: string;
  name: string;
  description: string;
  secret_fields: { key: string; label: string }[];
  secret_field_groups: string[][];
  supports_base_url: boolean;
  fields: ProviderField[];
  credential_file?: { key: string; label: string; accept: string; max_bytes: number };
};
type AgentProviderDefinition = {
  id: string;
  name: string;
  base_url: string;
  default_model: string;
  api_key_pattern?: string | null;
};

const CONFIG_SCHEMA = CONFIG_SCHEMA_JSON as unknown as {
  system_id: string;
  roles: { id: string; name: string }[];
  providers: ProviderDefinition[];
  agent_providers: AgentProviderDefinition[];
  agent_fields: unknown[];
  global_configs: unknown[];
};
const PROVIDERS = CONFIG_SCHEMA.providers;
const AGENT_PROVIDERS = CONFIG_SCHEMA.agent_providers;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    authorize(req);
    const parts = new URL(req.url).pathname.split("/").filter(Boolean);
    const root = parts.indexOf("arcreel-admin");
    const path = root >= 0 ? parts.slice(root + 1) : parts;
    if (req.method === "GET" && path[0] === "schema") {
      return json(CONFIG_SCHEMA);
    }
    if (path[0] === "global-configs" && path[1] === "character-catalog") {
      if (req.method === "GET") return await getCharacterCatalog();
      if (req.method === "PUT") return await putCharacterCatalog(req);
      if (req.method === "DELETE") return await deleteCharacterCatalog();
    }
    if (path[0] !== "accounts") return notFound();
    if (req.method === "GET" && path.length === 1) return await listAccounts(req);
    if (req.method === "POST" && path.length === 1) return await createAccount(req);
    const accountId = path[1];
    if (!accountId) return notFound();
    if (req.method === "PATCH" && path.length === 2) return await updateAccount(req, accountId);
    if (req.method === "POST" && path[2] === "reset-password") return await resetPassword(req, accountId);
    if (path[2] === "agent-credential") {
      if (req.method === "GET") return await getAgentCredential(accountId);
      if (req.method === "PUT") return await putAgentCredential(req, accountId);
      if (req.method === "DELETE") return await deleteAgentCredential(accountId);
    }
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
  const client = admin();
  const { data: existing, error: existingError } = await client.from("arcreel_provider_credentials")
    .select("encrypted_payload,revision").eq("user_id", accountId).eq("provider_id", providerId).maybeSingle();
  if (existingError) throw existingError;
  const payload: Record<string, string> = existing
    ? await decryptPayload(String(existing.encrypted_payload))
    : { name: "数据中台分配" };
  if (optional(body.name)) payload.name = optional(body.name)!;
  const touchedGroups = provider.secret_field_groups.filter((group) => group.some((key) => optional(body[key])));
  if (touchedGroups.length > 1) {
    throw new HttpError(400, "CREDENTIAL_GROUP_AMBIGUOUS", "一次只能填写一种密钥组合");
  }
  for (const field of provider.secret_fields) {
    const value = optional(body[field.key]);
    if (value) payload[field.key] = value;
  }
  if (provider.credential_file) {
    const contents = optional(body[provider.credential_file.key]);
    if (contents) {
      if (new TextEncoder().encode(contents).length > provider.credential_file.max_bytes) {
        throw new HttpError(413, "CREDENTIAL_FILE_TOO_LARGE", "Vertex 服务账号 JSON 不能超过 1 MiB");
      }
      try {
        const parsed = JSON.parse(contents);
        if (!parsed || typeof parsed !== "object" || !parsed.project_id) throw new Error("project_id");
      } catch {
        throw new HttpError(400, "VERTEX_CREDENTIAL_INVALID", "Vertex 服务账号 JSON 格式无效或缺少 project_id");
      }
      payload[provider.credential_file.key] = contents;
    }
    if (!payload[provider.credential_file.key]) {
      throw new HttpError(400, "CREDENTIAL_INCOMPLETE", "请上传 Vertex 服务账号 JSON");
    }
  } else if (!provider.secret_field_groups.some((group) => group.every((key) => payload[key]))) {
    throw new HttpError(400, "CREDENTIAL_INCOMPLETE", "请完整填写一种可用的密钥组合");
  }
  if (touchedGroups.length === 1 && touchedGroups[0].every((key) => payload[key])) {
    const selected = new Set(touchedGroups[0]);
    for (const group of provider.secret_field_groups) {
      for (const key of group) if (!selected.has(key)) delete payload[key];
    }
  }
  applyOptionalField(payload, body, "base_url", "url");
  for (const field of provider.fields) {
    applyOptionalField(payload, body, field.key, field.type);
    if (field.required && !payload[field.key]) {
      throw new HttpError(400, "PROVIDER_FIELD_REQUIRED", `${field.label} 不能为空`);
    }
  }
  const encryptedPayload = await encryptPayload(payload);
  const secretKeys = new Set(provider.secret_fields.map((item) => item.key));
  if (provider.credential_file) secretKeys.add(provider.credential_file.key);
  const maskedHint = Object.fromEntries(Object.entries(payload)
    .filter(([key]) => key !== "name")
    .map(([key, value]) => [key, secretKeys.has(key) ? (key === "credentials_json" ? "已上传 JSON" : mask(value)) : value]));
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

async function getAgentCredential(accountId: string) {
  await requireAccount(accountId);
  const { data, error } = await admin().from("arcreel_agent_credentials")
    .select("masked_hint,revision,updated_at").eq("user_id", accountId).maybeSingle();
  if (error) throw error;
  return json({ account_id: accountId, configured: Boolean(data), credential: data ?? null });
}

async function putAgentCredential(req: Request, accountId: string) {
  await requireAccount(accountId);
  const body = await readJson(req);
  const presetId = String(body.preset_id ?? "").trim();
  const preset = AGENT_PROVIDERS.find((item) => item.id === presetId);
  if (!preset) throw new HttpError(400, "AGENT_PROVIDER_INVALID", "请选择有效的 Agent 供应商");
  const client = admin();
  const { data: existing, error: existingError } = await client.from("arcreel_agent_credentials")
    .select("encrypted_payload,revision").eq("user_id", accountId).maybeSingle();
  if (existingError) throw existingError;
  const payload: Record<string, string> = existing ? await decryptPayload(String(existing.encrypted_payload)) : {};
  const submittedApiKey = optional(body.api_key);
  if (payload.preset_id && payload.preset_id !== presetId && !submittedApiKey) {
    throw new HttpError(400, "AGENT_API_KEY_REQUIRED", "切换 Agent 供应商时必须填写新的 API Key");
  }
  const apiKey = submittedApiKey || payload.api_key;
  if (!apiKey) throw new HttpError(400, "AGENT_API_KEY_REQUIRED", "请输入 Agent API Key");
  if (preset.api_key_pattern && !new RegExp(preset.api_key_pattern).test(apiKey)) {
    throw new HttpError(400, "AGENT_API_KEY_INVALID", "API Key 格式与所选 Agent 供应商不匹配");
  }
  const fallbackBaseUrl = presetId === "__custom__" ? "" : preset.base_url;
  const baseUrl = validateHttpUrl(String(optional(body.base_url) || payload.base_url || fallbackBaseUrl), "Agent 服务地址无效");
  payload.preset_id = presetId;
  payload.display_name = optional(body.display_name) || payload.display_name || preset.name;
  payload.base_url = baseUrl;
  payload.api_key = apiKey;
  for (const key of ["model", "haiku_model", "sonnet_model", "opus_model", "subagent_model"]) {
    if (key in body) {
      const value = optional(body[key]);
      if (value) payload[key] = value;
      else delete payload[key];
    }
  }
  if (!payload.model && preset.default_model) payload.model = preset.default_model;
  const revision = Number(existing?.revision || 0) + 1;
  const { data, error } = await client.from("arcreel_agent_credentials").upsert({
    user_id: accountId,
    encrypted_payload: await encryptPayload(payload),
    masked_hint: {
      preset_id: presetId,
      display_name: payload.display_name,
      base_url: baseUrl,
      api_key: mask(apiKey),
      model: payload.model || "",
      haiku_model: payload.haiku_model || "",
      sonnet_model: payload.sonnet_model || "",
      opus_model: payload.opus_model || "",
      subagent_model: payload.subagent_model || "",
    },
    revision,
    updated_at: new Date().toISOString(),
  }, { onConflict: "user_id" }).select("masked_hint,revision,updated_at").single();
  if (error) throw error;
  return json({ account_id: accountId, configured: true, credential: data });
}

async function deleteAgentCredential(accountId: string) {
  await requireAccount(accountId);
  const { error } = await admin().from("arcreel_agent_credentials").delete().eq("user_id", accountId);
  if (error) throw error;
  return new Response(null, { status: 204, headers: cors });
}

async function getCharacterCatalog() {
  const { data, error } = await admin().from("arcreel_global_configs")
    .select("masked_hint,revision,updated_at").eq("config_key", "character_catalog").maybeSingle();
  if (error) throw error;
  return json({ config_key: "character_catalog", scope: "global", configured: Boolean(data), config: data ?? null });
}

async function putCharacterCatalog(req: Request) {
  const body = await readJson(req);
  const apiUrl = validateHttpUrl(String(body.api_url ?? "").trim(), "人物资产渠道地址无效");
  const apiToken = String(body.api_token ?? "").trim();
  if (!apiToken) throw new HttpError(400, "CHARACTER_CATALOG_TOKEN_REQUIRED", "请输入人物资产渠道 Token");
  const payload = { api_url: apiUrl, api_token: apiToken };
  const client = admin();
  const { data: existing } = await client.from("arcreel_global_configs")
    .select("revision").eq("config_key", "character_catalog").maybeSingle();
  const revision = Number(existing?.revision || 0) + 1;
  const { data, error } = await client.from("arcreel_global_configs").upsert({
    config_key: "character_catalog",
    encrypted_payload: await encryptPayload(payload),
    masked_hint: { api_url: apiUrl, api_token: mask(apiToken) },
    revision,
    updated_at: new Date().toISOString(),
  }, { onConflict: "config_key" }).select("masked_hint,revision,updated_at").single();
  if (error) throw error;
  return json({ config_key: "character_catalog", scope: "global", configured: true, config: data });
}

async function deleteCharacterCatalog() {
  const { error } = await admin().from("arcreel_global_configs").delete().eq("config_key", "character_catalog");
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

async function decryptPayload(encoded: string): Promise<Record<string, string>> {
  const parts = encoded.split(".");
  if (parts.length !== 2) throw new Error("invalid encrypted payload");
  const keyBytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(required("ARCREEL_CREDENTIAL_ENCRYPTION_KEY")));
  const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromBase64(parts[0]) }, key, fromBase64(parts[1]));
  return JSON.parse(new TextDecoder().decode(plain));
}

function toBase64(value: Uint8Array) {
  return btoa(String.fromCharCode(...value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64(value: string) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

function applyOptionalField(
  payload: Record<string, string>, body: Record<string, unknown>, key: string, type: string,
) {
  if (!(key in body)) return;
  const value = optional(body[key]);
  if (!value) {
    delete payload[key];
    return;
  }
  if (type === "url") validateHttpUrl(value, `${key} 地址无效`);
  if (type === "number") {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0 || (key.endsWith("_max_workers") && !Number.isInteger(number || 0))) {
      throw new HttpError(400, "PROVIDER_NUMBER_INVALID", `${key} 数值无效`);
    }
    if (key.endsWith("_max_workers") && number < 1) {
      throw new HttpError(400, "PROVIDER_NUMBER_INVALID", `${key} 必须是正整数`);
    }
  }
  payload[key] = value;
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

function validateHttpUrl(value: string, message: string) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new Error("protocol");
    return parsed.toString().replace(/\/$/, "");
  } catch {
    throw new HttpError(400, "URL_INVALID", message);
  }
}
