/**
 * API transport: request wrappers, authentication headers, unauthorized handling, SSE glue and readonly gates.
 * Readonly state is a module singleton shared by all requests sitewide.
 */

import { getToken, clearToken } from "@/utils/auth";
import type { SseStreamError } from "@/utils/sse-stream";
import { isDemoProject } from "@/onboarding/demo-project";
import i18n from "@/i18n";
import {
  AgentFailureError,
  ApiRequestError,
  ReadOnlyModeError,
  ReferenceProjectionError,
  ScriptEditCommandError,
  SpeechAdmissionError,
  isAgentFailureDetail,
  isReferenceProjectionAdmission,
  isScriptEditResult,
  isSpeechAdmission,
  messageFromDetail,
  type ErrorResponse,
} from "./errors";

export const API_BASE = "/api/v1";

/**
 * Check fetch statuses, throwing Error with backend error information.
 * For custom fetch paths bypassing API.request().
 */
export async function throwIfNotOk(response: Response, fallbackMsg: string): Promise<void> {
  if (!response.ok) {
    handleUnauthorized(response);
    const error = await response
      .json()
      .catch(() => ({ detail: response.statusText })) as ErrorResponse;
    const detail = error.detail;
    if (isReferenceProjectionAdmission(detail)) {
      throw new ReferenceProjectionError(detail);
    }
    if (isSpeechAdmission(detail)) {
      throw new SpeechAdmissionError(detail);
    }
    throw new ApiRequestError(messageFromDetail(detail, fallbackMsg), error.diagnostic, response.status);
  }
}

export function handleUnauthorized(response: Response): void {
  if (response.status !== 401) return;
  redirectToLogin();
  throw new Error(i18n.t("errors:session_expired"));
}

function redirectToLogin(): void {
  clearToken();
  // Carry current in-app addresses for postlogin returns; attach from only for /app/ pages,
  // excluding login/nonapplication destinations.
  const current = `${globalThis.location.pathname}${globalThis.location.search}${globalThis.location.hash}`;
  globalThis.location.href = current.startsWith("/app/")
    ? `/login?from=${encodeURIComponent(current)}`
    : "/login";
}

let apiReadOnly = false;

/**
 * Enter/leave read-only mode (onboarding demo workbenches). Reject all non-GET/HEAD requests before sending while read-only.
 *
 * Demo workbenches render fake data using real components; write entries are already hidden. This gate is a structural fallback:
 * missed entries produce explicit exceptions instead of requests that actually write to user projects.
 */
export function setApiReadOnly(readOnly: boolean): void {
  apiReadOnly = readOnly;
}

// Static archive-import endpoint, rather than projects named import; project names permit letters, digits and hyphens,
// and `import` is valid (ProjectManager.normalize_project_name does not exclude it).
// Match exact paths rather than blacklisting names, avoiding misclassifying `/projects/import/...`
// (writes to real projects named import) as requests without project ownership.
const RESERVED_PROJECT_ENDPOINTS = new Set(["/projects/import"]);

// System endpoints without project ownership that write no project data: gates default to blocking all unowned
// writes (global asset libraries, provider credentials, etc.); this is the sole narrow exemption. Leaving onboarding tours writes
// this seen marker while still on demo routes (before apiReadOnly resets). It affects only current-user onboarding
// state, outside the accidental demo/other-project data writes this gate prevents.
const READ_ONLY_GATE_EXEMPT_ENDPOINTS = new Set(["/onboarding/seen"]);

/** Extract project names from endpoints like `/projects/{name}` or `/projects/{name}/...`; return null for non-project paths/static reserved endpoints. */
function extractProjectName(endpoint: string): string | null {
  if (RESERVED_PROJECT_ENDPOINTS.has(endpoint)) return null;
  const match = /^\/projects\/([^/?]+)/.exec(endpoint);
  if (!match) return null;
  return decodeURIComponent(match[1]);
}

/**
 * Whether read-only gates should block requests. Allow writes targeting specific real projects:
 * demo mode may have started just before requests were sent, but cannot block operations already initiated from real projects;
 * continue blocking requests targeting demos or lacking project ownership (global asset libraries, etc.),
 * except narrowly exempted system endpoints.
 */
function isReadOnlyGateBlocking(endpoint: string): boolean {
  if (!apiReadOnly) return false;
  if (READ_ONLY_GATE_EXEMPT_ENDPOINTS.has(endpoint)) return false;
  const projectName = extractProjectName(endpoint);
  return projectName === null || isDemoProject(projectName);
}

/** Inject Authorization headers into fetch options. */
export function withAuth(endpoint: string, options: RequestInit = {}): RequestInit {
  const method = (options.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && isReadOnlyGateBlocking(endpoint)) {
    throw new ReadOnlyModeError(method);
  }
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // Add Accept-Language header based on current i18n language
  headers.set("Accept-Language", i18n.language || "zh");
  return { ...options, headers };
}

/** SSE connection headers: share credentials/language with {@link withAuth}, reading them anew before every reconnect. */
export function sseHeaders(): HeadersInit {
  const headers: Record<string, string> = { "Accept-Language": i18n.language || "zh" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

export function parseSseJson(data: string, label: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(data || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch (err) {
    console.error(`Failed to parse ${label} SSE data:`, err, data);
    return null;
  }
}

/** Handle server 401 event-stream rejections like ordinary requests: clear credentials and return to login. */
export function sseErrorHandler(onError?: (error: SseStreamError) => void) {
  return (error: SseStreamError) => {
    if (error.status === 401) {
      redirectToLogin();
    }
    onError?.(error);
  };
}

/**
 * Generic requests: prepend API prefixes, inject credentials and apply read-only gates; throw corresponding contract errors for non-2xx responses.
 */
export async function requestJson<T = unknown>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const defaultOptions: RequestInit = {
    headers: {
      "Content-Type": "application/json",
    },
  };

  const response = await fetch(url, withAuth(endpoint, { ...defaultOptions, ...options }));

  if (!response.ok) {
    handleUnauthorized(response);
    const payload = await response
      .json()
      .catch(() => ({ detail: response.statusText })) as unknown;
    if (isScriptEditResult(payload)) {
      throw new ScriptEditCommandError(payload);
    }
    const error = payload as ErrorResponse;
    if (isScriptEditResult(error.detail)) {
      throw new ScriptEditCommandError(error.detail);
    }
    if (isAgentFailureDetail(error.detail)) {
      throw new AgentFailureError(error.detail.message, error.detail.failure);
    }
    if (isReferenceProjectionAdmission(error.detail)) {
      throw new ReferenceProjectionError(error.detail);
    }
    if (isSpeechAdmission(error.detail)) {
      throw new SpeechAdmissionError(error.detail);
    }
    throw new ApiRequestError(messageFromDetail(error.detail, i18n.t("errors:request_failed")), error.diagnostic, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
