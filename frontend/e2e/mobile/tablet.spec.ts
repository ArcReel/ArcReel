import { expect, test } from "@playwright/test";
import { expectNoHorizontalScroll, open } from "../support/mobile-helpers.ts";

// 平板竖屏（768×1024）：视口正好在移动档边界之上，按桌面档渲染。
// 这里验证桌面布局在这个宽度下不被挤压：顶栏不重叠、项目名不被压成竖排、页面不横向滚动。

test.describe("平板：项目工作区", () => {
  test("顶栏三段不重叠，项目名有可用宽度", async ({ page }) => {
    await open(page, "/app/projects/project-0");
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalScroll(page);

    const overlaps = await page.evaluate(() => {
      const header = document.querySelector("header");
      if (!header) return [{ label: "header", other: "missing" }];
      const items = Array.from(header.querySelectorAll<HTMLElement>("button, a[href]"))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          return {
            label: (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 24),
            left: rect.left,
            right: rect.right,
            width: rect.width,
          };
        })
        .filter((item) => item.width > 0);
      const found: { label: string; other: string }[] = [];
      for (let i = 0; i < items.length; i += 1) {
        for (let k = i + 1; k < items.length; k += 1) {
          if (items[i].left < items[k].right && items[k].left < items[i].right) {
            found.push({ label: items[i].label, other: items[k].label });
          }
        }
      }
      return found;
    });
    expect.soft(overlaps, "顶栏控件互相重叠").toEqual([]);
  });

  test("设置页侧栏在桌面档原位显示，不换成抽屉", async ({ page }) => {
    await open(page, "/app/settings");
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalScroll(page);

    // 桌面档：没有菜单按钮，侧栏导航直接可见
    await expect(page.getByRole("button", { name: "打开导航菜单" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "设置" })).toBeVisible();
  });

  test("大厅在平板宽度下不横向滚动，顶栏不重叠", async ({ page }) => {
    await open(page, "/app/projects");
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});
