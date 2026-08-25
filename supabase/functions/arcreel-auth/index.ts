import "@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "@supabase/supabase-js";

type Profile = {
  id: string;
  username: string;
  display_name: string | null;
  role: "admin" | "user";
  status: "active" | "disabled";
  auth_email: string;
};

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const action = new URL(req.url).pathname.split("/").filter(Boolean).at(-1) ?? "";
    if (req.method === "POST" && action === "login") return await login(req);
    if (req.method === "POST" && action === "refresh") return await refresh(req);
    if (req.method === "GET" && action === "me") return await me(req);
    if (req.method === "GET" && action === "config") return await config(req);
    return json({ error: { code: "NOT_FOUND", message: "接口不存在" } }, 404);
  } catch (error) {
    if (error instanceof HttpError) return json({ error: { code: error.code, message: error.message } }, error.status);
    console.error("arcreel-auth", error);
    return json({ error: { code: "INTERNAL_ERROR", message: "服务暂时不可用" } }, 500);
  }
});

async function login(req: Request) {
  const body = await readJson(req);
  const username = String(body.username ?? "").trim();
  const password = String(body.password ?? "");
  if (!username || !password) throw new HttpError(400, "CREDENTIALS_REQUIRED", "请输入账号和密码");
  const { data: profile } = await adminClient()
    .from("arcreel_profiles")
    .select("*")
    .ilike("username", username)
    .maybeSingle<Profile>();
  if (!profile || profile.status !== "active") return invalidCredentials();
  const { data, error } = await publicClient(req).auth.signInWithPassword({ email: profile.auth_email, password });
  if (error || !data.session) return invalidCredentials();
  return json(sessionResponse(data.session, profile));
}

async function refresh(req: Request) {
  const body = await readJson(req);
  const refreshToken = String(body.refresh_token ?? "");
  if (!refreshToken) throw new HttpError(400, "REFRESH_TOKEN_REQUIRED", "缺少刷新令牌");
  const { data, error } = await publicClient(req).auth.refreshSession({ refresh_token: refreshToken });
  if (error || !data.session || !data.user) throw new HttpError(401, "SESSION_EXPIRED", "登录已过期，请重新登录");
  const profile = await activeProfile(data.user.id);
  if (!profile) throw new HttpError(403, "ACCOUNT_DISABLED", "账号已停用");
  return json(sessionResponse(data.session, profile));
}

async function me(req: Request) {
  return json({ user: publicProfile(await authenticatedProfile(req)) });
}

async function config(req: Request) {
  const profile = await authenticatedProfile(req);
  const { data, error } = await adminClient()
    .from("arcreel_provider_credentials")
    .select("provider_id,encrypted_payload,revision,updated_at")
    .eq("user_id", profile.id)
    .order("provider_id");
  if (error) throw error;
  const credentials: Record<string, string>[] = [];
  let revision = 0;
  for (const row of data ?? []) {
    credentials.push({ provider_id: row.provider_id, ...await decryptPayload(String(row.encrypted_payload)) });
    revision = Math.max(revision, Number(row.revision) || 0);
  }
  return json({ user: publicProfile(profile), revision, credentials });
}

async function authenticatedProfile(req: Request): Promise<Profile> {
  const token = req.headers.get("Authorization")?.replace(/^Bearer\s+/i, "").trim() ?? "";
  if (!token) throw new HttpError(401, "TOKEN_REQUIRED", "缺少登录凭证");
  const { data, error } = await adminClient().auth.getUser(token);
  if (error || !data.user) throw new HttpError(401, "TOKEN_INVALID", "登录凭证无效或已过期");
  const profile = await activeProfile(data.user.id);
  if (!profile) throw new HttpError(403, "ACCOUNT_DISABLED", "账号已停用");
  return profile;
}

async function activeProfile(id: string): Promise<Profile | null> {
  const { data } = await adminClient().from("arcreel_profiles").select("*").eq("id", id).eq("status", "active").maybeSingle<Profile>();
  return data ?? null;
}

function sessionResponse(session: { access_token: string; refresh_token: string; expires_in: number; expires_at?: number }, profile: Profile) {
  return {
    access_token: session.access_token,
    refresh_token: session.refresh_token,
    expires_in: session.expires_in,
    expires_at: session.expires_at,
    user: publicProfile(profile),
  };
}

function publicProfile(profile: Profile) {
  return { id: profile.id, username: profile.username, display_name: profile.display_name, role: profile.role };
}

function adminClient() {
  return createClient(required("SUPABASE_URL"), required("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

function publicClient(req: Request) {
  return createClient(required("SUPABASE_URL"), req.headers.get("apikey") || required("SUPABASE_ANON_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function decryptPayload(encoded: string): Promise<Record<string, string>> {
  const parts = encoded.split(".");
  if (parts.length !== 2) throw new Error("invalid encrypted payload");
  const keyBytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(required("ARCREEL_CREDENTIAL_ENCRYPTION_KEY")));
  const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromBase64(parts[0]) }, key, fromBase64(parts[1]));
  return JSON.parse(new TextDecoder().decode(plain));
}

function fromBase64(value: string) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

function invalidCredentials() {
  return json({ error: { code: "INVALID_CREDENTIALS", message: "账号或密码错误" } }, 401);
}

async function readJson(req: Request): Promise<Record<string, unknown>> {
  try { return await req.json(); } catch { throw new HttpError(400, "INVALID_JSON", "请求格式无效"); }
}

class HttpError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) { super(message); }
}

function required(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}
