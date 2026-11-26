// 本文件同时被 Node 直接运行的录制脚本引用，只能写可擦除的 TypeScript 语法。
import { appendFileSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export const RECORDED_DIR = join(import.meta.dirname, "..", "fixtures", "recorded");

/** 套件的固定时间；录制时后端返回的时间戳也统一改写成它，重录不产生无关 diff。 */
export const FIXED_NOW = "2026-01-01T08:00:00.000Z";

export const RECORDED_ACCESS_TOKEN = "e2e-access-token";

export interface RecordedResponse {
  method: string;
  path: string;
  status: number;
  body: unknown;
}

export function recordedKey(method: string, url: string): string {
  const parsed = new URL(url, "http://recorded.invalid");
  parsed.searchParams.sort();
  return `${method.toUpperCase()} ${parsed.pathname}${parsed.search}`;
}

type Hit = "route" | "direct" | "override";

function traceHit(file: string, hit: Hit) {
  const output = process.env.E2E_RECORDING_TRACE;
  if (output) appendFileSync(output, `${file}\t${hit}\n`);
}

export function loadRecordedResponses() {
  const responses = new Map<string, { file: string; response: RecordedResponse }>();
  for (const file of readdirSync(RECORDED_DIR).filter((name) => name.endsWith(".json")).sort()) {
    const response = JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse;
    responses.set(recordedKey(response.method, response.path), { file, response });
  }
  return {
    get(key: string, hit: Hit = "direct") {
      const entry = responses.get(key);
      if (entry) traceHit(entry.file, hit);
      return entry?.response;
    },
  };
}

export function recorded<T>(file: string): T {
  traceHit(file, "direct");
  return (JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse).body as T;
}
