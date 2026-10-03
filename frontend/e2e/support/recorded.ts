// 录制的接口数据替身：由 e2e/record-fixtures.ts 对真实后端录制，页面级套件经 page.route() 回放。
// 本文件同时被 Node 直接运行的录制脚本引用，只能写可擦除的 TypeScript 语法。
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export const RECORDED_DIR = join(import.meta.dirname, "..", "fixtures", "recorded");

/** 套件的固定时间；录制时后端返回的时间戳也统一改写成它，重录不产生无关 diff。 */
export const FIXED_NOW = "2026-01-01T08:00:00.000Z";

/** 录制时签发的访问令牌改写成固定值，已登录场景把它写进 localStorage。 */
export const RECORDED_ACCESS_TOKEN = "e2e-access-token";

export interface RecordedResponse {
  method: string;
  path: string;
  status: number;
  body: unknown;
}

export function recordedKey(method: string, path: string): string {
  return `${method.toUpperCase()} ${path}`;
}

export function loadRecordedResponses(): Map<string, RecordedResponse> {
  const responses = new Map<string, RecordedResponse>();
  for (const file of readdirSync(RECORDED_DIR).filter((name) => name.endsWith(".json")).sort()) {
    const recorded = JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse;
    responses.set(recordedKey(recorded.method, recorded.path), recorded);
  }
  return responses;
}
