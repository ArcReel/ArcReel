// Page-suite test automatically replays recorded APIs, fixes time and blocks external requests per case.
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";
import { inspectLayout } from "./layout-probe.ts";
import { FIXED_NOW, loadRecordedResponses, recordedKey, type RecordedResponse } from "./recorded.ts";

const RECORDED = loadRecordedResponses();

/**
 * Scenario API overrides for long-text/many-item stress variants; keys like `GET /api/v1/projects` include full query strings when present.
 * String `body` returns unchanged as text/plain, others as JSON.
 */
export type ApiOverrides = Record<string, Pick<RecordedResponse, "status" | "body">>;

export interface ApiStub {
  override(overrides: ApiOverrides): void;
}

function isLoopback(url: URL) {
  return url.hostname === "127.0.0.1" || url.hostname === "localhost";
}

export const test = base.extend<{ api: ApiStub }>({
  api: [
    async ({ page }, use) => {
      const overrides = new Map<string, Pick<RecordedResponse, "status" | "body">>();
      const unrecorded: string[] = [];

      await page.clock.setFixedTime(new Date(FIXED_NOW));
      // External resources such as online fonts are outside assertions; block them to avoid network variability.
      await page.route(
        (url) => !isLoopback(url),
        (route) => route.abort(),
      );
      await page.route("**/api/**", async (route) => {
        const request = route.request();
        const key = recordedKey(request.method(), request.url());
        const response = overrides.get(key) ?? RECORDED.get(key);
        if (!response) {
          unrecorded.push(key);
          await route.fulfill({ status: 501, json: { detail: `没有录制 ${key}` } });
          return;
        }
        // Memory-file bodies and similar endpoints return text/plain originals; replacement response bodies use strings.
        if (typeof response.body === "string") {
          await route.fulfill({ status: response.status, body: response.body, contentType: "text/plain; charset=utf-8" });
          return;
        }
        await route.fulfill({ status: response.status, json: response.body });
      });

      await use({
        override(entries) {
          for (const [key, response] of Object.entries(entries)) {
            const [method, url] = key.split(" ", 2);
            overrides.set(recordedKey(method, url), response);
          }
        },
      });

      expect(unrecorded, "页面请求了没有录制的接口：运行 pnpm e2e:record 补录，或在场景里提供替换响应").toEqual(
        [],
      );
    },
    { auto: true },
  ],
});

export { expect };

/** Documents themselves never scroll; no overflow-clipped unreachable contents or undeclared horizontal scrolling. */
export async function expectReachableLayout(page: Page) {
  const report = await page.evaluate(inspectLayout);
  expect
    .soft(report.documentScrollHeight, "文档本身发生了滚动：外壳根节点或绝对定位元素撑高了文档")
    .toBe(report.viewportHeight);
  expect.soft(report.documentScrollWidth, "文档本身发生了横向滚动：有元素宽于视口").toBe(report.viewportWidth);
  expect
    .soft(
      report.strayScrollX,
      "纵向滚动区（含弹层正文）被宽内容撑出了横向滚动：内容应折行或限宽，确需横向滚动的区域写 overflow-x-auto",
    )
    .toEqual([]);
  expect(report.clipped, "有内容被 overflow 裁切且无法滚动到达").toEqual([]);
}

// axe WCAG 2.2 rules (including target-size) default off; explicitly enable wcag22aa.
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const violations = results.violations.map((violation) => ({
    rule: violation.id,
    help: violation.help,
    targets: violation.nodes.map((node) => node.target.join(" ")),
  }));
  expect(violations, "axe 报告了可访问性问题").toEqual([]);
}
