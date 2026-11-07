// Recorded API fixtures captured by e2e/record-fixtures.ts against real backends, replayed with page.route() in page suites.
// Node recording scripts import this file directly too; use erasable TypeScript syntax only.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export const RECORDED_DIR = join(import.meta.dirname, "..", "fixtures", "recorded");

/** Fixed suite time; recorded backend timestamps normalize to it, preventing unrelated rerecord diffs. */
export const FIXED_NOW = "2026-01-01T08:00:00.000Z";

/** Recording-issued access tokens normalize to fixed values, stored in localStorage by authenticated scenarios. */
export const RECORDED_ACCESS_TOKEN = "e2e-access-token";

export interface RecordedResponse {
  method: string;
  path: string;
  status: number;
  body: unknown;
}

/**
 * Fixture keys: methods plus paths/queries, sorting parameters by name independently of dispatch order/encoding;
 * distinct queries are distinct interfaces, recorded individually. `url` permits complete addresses or /-prefixed paths.
 */
export function recordedKey(method: string, url: string): string {
  const parsed = new URL(url, "http://recorded.invalid");
  parsed.searchParams.sort();
  return `${method.toUpperCase()} ${parsed.pathname}${parsed.search}`;
}

export function loadRecordedResponses(): Map<string, RecordedResponse> {
  const responses = new Map<string, RecordedResponse>();
  for (const file of readdirSync(RECORDED_DIR).filter((name) => name.endsWith(".json")).sort()) {
    const recorded = JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse;
    responses.set(recordedKey(recorded.method, recorded.path), recorded);
  }
  return responses;
}

/** Recorded response bodies, letting regional stress variants modify real interface shapes. */
export function recorded<T>(file: string): T {
  return (JSON.parse(readFileSync(join(RECORDED_DIR, file), "utf8")) as RecordedResponse).body as T;
}
