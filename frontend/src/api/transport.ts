/**
 * API 传输层：请求封装、鉴权头、未授权处理、SSE 胶水与只读闸门。
 * 只读闸门的状态是本模块的单例，全站所有请求共用这一份。
 */

import { getToken, clearToken } from "@/utils/auth";
import type { SseStreamError } from "@/utils/sse-stream";
import { isDemoProject } from "@/onboarding/demo-project";
import i18n from "@/i18n";
import {
  AgentFailureError,
  ApiRequestError,
  NarratedVideoDurationError,
  ReadOnlyModeError,
  ReferenceProjectionError,
  ScriptEditCommandError,
  SpeechAdmissionError,
  isAgentFailureDetail,
  isNarratedVideoDurationAdmission,
  isReferenceProjectionAdmission,
  isScriptEditResult,
  isSpeechAdmission,
  messageFromDetail,
  type ErrorResponse,
} from "./errors";

export const API_BASE = "/api/v1";

/**
 * 检查 fetch 响应状态，抛出包含后端错误信息的 Error。
 * 用于不经过 API.request() 的自定义 fetch 调用。
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
    if (isNarratedVideoDurationAdmission(detail)) {
      throw new NarratedVideoDurationError(detail);
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
  throw new Error("认证已过期，请重新登录");
}

function redirectToLogin(): void {
  clearToken();
  // 携带当前所在的站内地址，登录成功后回跳；仅对 /app/ 下的页面附加 from，
  // 避免把登录页自身等非应用路径写进回跳参数。
  const current = `${globalThis.location.pathname}${globalThis.location.search}${globalThis.location.hash}`;
  globalThis.location.href = current.startsWith("/app/")
    ? `/login?from=${encodeURIComponent(current)}`
    : "/login";
}

let apiReadOnly = false;

/**
 * 进入 / 离开只读态（引导演示工作台）。只读期间任何非 GET / HEAD 请求会在发出前被拒绝。
 *
 * 演示工作台是用真组件渲染假数据，写操作的入口都已经不渲染；这道闸门是结构性兜底 ——
 * 漏掉一个入口时会得到一个明确的异常，而不是一条真写进用户项目的请求。
 */
export function setApiReadOnly(readOnly: boolean): void {
  apiReadOnly = readOnly;
}

// 静态归档导入端点，不是「项目名恰好叫 import」——项目名允许字母数字中划线，
// `import` 本身是合法项目名（ProjectManager.normalize_project_name 不排除它），
// 按精确路径匹配而非按名称黑名单，避免把 `/projects/import/...`（真实项目名为
// import 的写请求）一并误判成不带项目归属
const RESERVED_PROJECT_ENDPOINTS = new Set(["/projects/import"]);

// 不带项目归属、但本身不写入任何项目数据的系统级端点：闸门默认拦截所有无项目归属的
// 写请求（全局资产库、供应商凭证等），这里是唯一的窄豁免。引导 tour 退出时会在仍处于
// 演示路由（apiReadOnly 尚未复位）期间写这一条「已看过」标记，它只影响当前用户的引导
// 状态，不属于闸门要防的「误写演示态/其他项目数据」范畴。
const READ_ONLY_GATE_EXEMPT_ENDPOINTS = new Set(["/onboarding/seen"]);

/** 从形如 `/projects/{name}` 或 `/projects/{name}/...` 的 endpoint 中取出项目名；非项目路径或静态保留端点返回 null */
function extractProjectName(endpoint: string): string | null {
  if (RESERVED_PROJECT_ENDPOINTS.has(endpoint)) return null;
  const match = /^\/projects\/([^/?]+)/.exec(endpoint);
  if (!match) return null;
  return decodeURIComponent(match[1]);
}

/**
 * 只读闸门是否应拦截这次请求。指向某个具体真实项目的写请求放行 ——
 * 演示态可能在该请求发出前才切入，但它拦不住已经从真实项目发起的操作；
 * 指向演示项目本身或不带项目归属的请求（全局资产库等）仍按闸门原意拦截，
 * 窄豁免名单中的系统端点除外。
 */
function isReadOnlyGateBlocking(endpoint: string): boolean {
  if (!apiReadOnly) return false;
  if (READ_ONLY_GATE_EXEMPT_ENDPOINTS.has(endpoint)) return false;
  const projectName = extractProjectName(endpoint);
  return projectName === null || isDemoProject(projectName);
}

/** 为 fetch options 注入 Authorization header */
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

/** SSE 建连请求头：与 {@link withAuth} 同一套凭证与语言，每次重建前重新取。 */
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
    console.error(`解析${label} SSE 数据失败:`, err, data);
    return null;
  }
}

/** 事件流被服务端以 401 拒绝时与普通请求同样处理：清凭证并回登录页。 */
export function sseErrorHandler(onError?: (error: SseStreamError) => void) {
  return (error: SseStreamError) => {
    if (error.status === 401) {
      redirectToLogin();
    }
    onError?.(error);
  };
}

/**
 * 通用请求：拼接 API 前缀、注入凭证并过只读闸门，非 2xx 时按错误契约抛出对应错误。
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
    if (isNarratedVideoDurationAdmission(error.detail)) {
      throw new NarratedVideoDurationError(error.detail);
    }
    if (isSpeechAdmission(error.detail)) {
      throw new SpeechAdmissionError(error.detail);
    }
    throw new ApiRequestError(messageFromDetail(error.detail, "请求失败"), error.diagnostic, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
