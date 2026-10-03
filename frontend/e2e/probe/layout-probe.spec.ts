import { expect, test, type Page } from "@playwright/test";
import { inspectLayout } from "../support/layout-probe.ts";

// 合成页面固定 1280×720：外壳按视口铺满，内容高度远超视口。
const SHELL_STYLE = "html,body{margin:0;height:100%}*{box-sizing:border-box}";

async function render(page: Page, body: string) {
  await page.setContent(`<!doctype html><style>${SHELL_STYLE}</style><body>${body}</body>`);
  return page.evaluate(inspectLayout);
}

const TALL = '<div style="height:2000px"></div>';

test("flex 链中间层漏设 min-height:0 时报告裁切它的外壳", async ({ page }) => {
  const report = await render(
    page,
    `<div id="shell" style="height:100%;display:flex;flex-direction:column;overflow:hidden">
      <div id="middle" style="flex:1;display:flex;flex-direction:column">
        <div style="flex:1;overflow:auto">${TALL}</div>
      </div>
    </div>`,
  );

  expect(report.clipped).toEqual([
    expect.objectContaining({ path: expect.stringContaining("div#shell"), axis: "y" }),
  ]);
});

test("flex 链补上 min-height:0 后内容可在内层滚动，不报告", async ({ page }) => {
  const report = await render(
    page,
    `<div style="height:100%;display:flex;flex-direction:column;overflow:hidden">
      <div style="flex:1;min-height:0;display:flex;flex-direction:column">
        <div style="flex:1;overflow:auto">${TALL}</div>
      </div>
    </div>`,
  );

  expect(report.clipped).toEqual([]);
});

test("overflow-x:hidden 吞掉横向内容时报告 x 轴", async ({ page }) => {
  const report = await render(
    page,
    `<div id="row" style="width:300px;overflow-x:hidden"><div style="width:900px;height:20px"></div></div>`,
  );

  expect(report.clipped).toEqual([expect.objectContaining({ path: "body > div#row", axis: "x" })]);
});

test("固定高度裁切时报告，并指出越界的最深后代", async ({ page }) => {
  const report = await render(
    page,
    `<section style="height:120px;overflow:hidden">
      <ul><li id="last" style="height:400px">条目</li></ul>
    </section>`,
  );

  expect(report.clipped).toEqual([
    expect.objectContaining({ path: "body > section", axis: "y", culprit: "body > section > ul > li#last" }),
  ]);
});

test("overflow:clip 与 hidden 同样视为裁切", async ({ page }) => {
  const report = await render(page, `<div style="height:120px;overflow:clip">${TALL}</div>`);

  expect(report.clipped).toEqual([expect.objectContaining({ axis: "y" })]);
});

test("不超过 1px 的取整误差不报告", async ({ page }) => {
  const report = await render(page, `<div style="height:100px;overflow:hidden"><div style="height:101px"></div></div>`);

  expect(report.clipped).toEqual([]);
});

test.describe("豁免", () => {
  test("不可见元素不报告", async ({ page }) => {
    const report = await render(
      page,
      `<div style="visibility:hidden"><div style="height:100px;overflow:hidden">${TALL}</div></div>
      <div style="opacity:0"><div style="height:100px;overflow:hidden">${TALL}</div></div>`,
    );

    expect(report.clipped).toEqual([]);
  });

  test("带 text-overflow:ellipsis 的单行横向溢出不报告", async ({ page }) => {
    const report = await render(
      page,
      `<p style="width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${"很长的项目名称".repeat(10)}</p>`,
    );

    expect(report.clipped).toEqual([]);
  });

  test("line-clamp 截断的多行文本不报告", async ({ page }) => {
    const report = await render(
      page,
      `<p style="width:200px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden">${"一段很长的描述文字。".repeat(20)}</p>`,
    );

    expect(report.clipped).toEqual([]);
  });

  test("data-overflow-ok 写明原因时豁免该元素及其后代", async ({ page }) => {
    const report = await render(
      page,
      `<div data-overflow-ok="装饰光晕" style="height:100px;overflow:hidden">
        <div style="height:50px;overflow:hidden">${TALL}</div>
      </div>`,
    );

    expect(report.clipped).toEqual([]);
  });

  test("data-overflow-ok 没写原因时不豁免", async ({ page }) => {
    const report = await render(page, `<div data-overflow-ok="" style="height:100px;overflow:hidden">${TALL}</div>`);

    expect(report.clipped).toEqual([expect.objectContaining({ axis: "y" })]);
  });
});

test("报告文档自身的滚动高度，用于断言文档不滚动", async ({ page }) => {
  const fits = await render(page, `<div style="height:100%"></div>`);
  const scrolls = await render(page, TALL);

  expect(fits.documentScrollHeight).toBe(fits.viewportHeight);
  expect(scrolls.documentScrollHeight).toBeGreaterThan(scrolls.viewportHeight);
});
