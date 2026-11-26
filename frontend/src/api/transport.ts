
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
  locateDiagnosticItems,
  type ErrorResponse,
} from "./errors";

export const API_BASE = "/api/v1";

function requestErrorMessage(error: ErrorResponse, fallback: string): string {
  return error.code === "asset_resource_busy"
    ? i18n.t("errors:asset_resource_busy")
    : messageFromDetail(error.detail, fallback);
}

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
    throw new ApiRequestError(requestErrorMessage(error, fallbackMsg), error.diagnostic, response.status);
  }
}

export function handleUnauthorized(response: Response): void {
  if (response.status !== 401) return;
  redirectToLogin();
  throw new Error(i18n.t("errors:session_expired"));
}

function redirectToLogin(): void {
  clearToken();
  const current = `${globalThis.location.pathname}${globalThis.location.search}${globalThis.location.hash}`;
  globalThis.location.href = current.startsWith("/app/")
    ? `/login?from=${encodeURIComponent(current)}`
    : "/login";
}

let apiReadOnly = false;

export function setApiReadOnly(readOnly: boolean): void {
  apiReadOnly = readOnly;
}

const RESERVED_PROJECT_ENDPOINTS = new Set(["/projects/import"]);

const READ_ONLY_GATE_EXEMPT_ENDPOINTS = new Set(["/onboarding/seen"]);

function extractProjectName(endpoint: string): string | null {
  if (RESERVED_PROJECT_ENDPOINTS.has(endpoint)) return null;
  const match = /^\/projects\/([^/?]+)/.exec(endpoint);
  if (!match) return null;
  return decodeURIComponent(match[1]);
}

function isReadOnlyGateBlocking(endpoint: string): boolean {
  if (!apiReadOnly) return false;
  if (READ_ONLY_GATE_EXEMPT_ENDPOINTS.has(endpoint)) return false;
  const projectName = extractProjectName(endpoint);
  return projectName === null || isDemoProject(projectName);
}

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
  headers.set("Accept-Language", i18n.language || "zh");
  return { ...options, headers };
}

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

export function sseErrorHandler(onError?: (error: SseStreamError) => void) {
  return (error: SseStreamError) => {
    if (error.status === 401) {
      redirectToLogin();
    }
    onError?.(error);
  };
}

export async function requestJson<T = unknown>(
  endpoint: string,
  options: RequestInit = {},
  onHeaders?: (headers: Headers) => void,
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
    const error = payload as ErrorResponse;
    const detail = isScriptEditResult(payload) ? payload : error.detail;
    if (isScriptEditResult(detail) || isSpeechAdmission(detail)) {
      await locateDiagnosticItems(extractProjectName(endpoint), detail, options.signal ?? undefined);
      throw isScriptEditResult(detail) ? new ScriptEditCommandError(detail) : new SpeechAdmissionError(detail);
    }
    if (isAgentFailureDetail(error.detail)) {
      throw new AgentFailureError(error.detail.message, error.detail.failure);
    }
    if (isReferenceProjectionAdmission(error.detail)) {
      throw new ReferenceProjectionError(error.detail);
    }
    throw new ApiRequestError(
      requestErrorMessage(error, i18n.t("errors:request_failed")),
      error.diagnostic,
      response.status,
      error.detail,
    );
  }

  onHeaders?.(response.headers);
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
