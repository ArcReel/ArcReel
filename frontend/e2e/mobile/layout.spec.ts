import { expect, test } from "@playwright/test";
import { expectNoHorizontalScroll, expectTouchTargets, open, stubApi } from "../support/mobile-helpers.ts";

// 移动端验收：在 375/390/414 三种视口上，逐个页面打开、点开抽屉与弹层，
// 断言文档不横向滚动、没有够不到的被裁内容、触控目标不小于 44×44。
// 768px 竖屏平板落在移动档边界之上，由 tablet.spec.ts 覆盖。

const LOBBY = "/app/projects";

test.describe("移动端：项目大厅", () => {
  test("打开大厅：无横向滚动，顶栏按钮可点，触控目标达标", async ({ page }) => {
    await open(page, LOBBY);
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    // 移动端顶栏：资产库入口收成图标，新建项目保留可点
    await expect(page.getByRole("button", { name: "新建项目" })).toBeVisible();
  });

  test("新建项目按钮打开创建向导，向导在移动端可用", async ({ page }) => {
    await open(page, LOBBY);
    await page.getByRole("button", { name: "新建项目" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);
    // 弹层贴底：底部与视口底部对齐
    const box = await dialog.boundingBox();
    const viewport = page.viewportSize();
    expect(box).not.toBeNull();
    expect(viewport).not.toBeNull();
    if (box && viewport) expect(Math.abs(box.y + box.height - viewport.height)).toBeLessThanOrEqual(1);
  });

  test("项目卡更多操作菜单在移动端完整可见", async ({ page }) => {
    await open(page, LOBBY);
    const firstCard = page.getByRole("list", { name: "项目" }).getByRole("listitem").first();
    await firstCard.getByRole("button").first().click();
    await expect(page.getByRole("menuitem", { name: "删除" })).toBeInViewport({ ratio: 1 });
  });
});

test.describe("移动端：设置页抽屉", () => {
  test("设置页侧栏收进抽屉，可打开、可关闭、无横向滚动", async ({ page }) => {
    await open(page, "/app/settings");
    await expect(page.getByRole("main")).toBeVisible();

    const openButton = page.getByRole("button", { name: "打开导航菜单" }).first();
    await expect(openButton).toBeVisible();
    await openButton.click();

    const drawer = page.getByRole("dialog", { name: "设置" });
    await expect(drawer).toBeVisible();
    await expectTouchTargets(page);

    // Esc 收起
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expectNoHorizontalScroll(page);
  });

  test("设置页切换分区后仍无横向滚动", async ({ page }) => {
    await open(page, "/app/settings");
    await page.getByRole("button", { name: "打开导航菜单" }).first().click();
    const drawer = page.getByRole("dialog", { name: "设置" });
    await drawer.getByRole("link", { name: /通用/ }).click();
    await expect(page.getByRole("main")).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});

test.describe("移动端：工作区", () => {
  test("项目工作区：侧栏为抽屉，Agent 面板为整屏层", async ({ page }) => {
    await open(page, "/app/projects/project-0");
    const main = page.getByRole("main");
    await expect(main).toBeVisible();
    await expectNoHorizontalScroll(page);

    // 顶栏菜单按钮打开工作区导航抽屉
    const navButton = page.getByRole("button", { name: "打开导航菜单" });
    await expect(navButton).toBeVisible();
    await navButton.click();
    const drawer = page.getByRole("dialog", { name: "工作区导航" });
    await expect(drawer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    // Agent 面板整屏覆盖
    const agentToggle = page.getByRole("button", { name: "Agent", exact: true });
    await agentToggle.click();
    await expect(page.getByRole("button", { name: "收起 Agent 面板" })).toBeVisible();
    await page.getByRole("button", { name: "收起 Agent 面板" }).click();
    await expect(page.getByRole("button", { name: "收起 Agent 面板" })).toBeHidden();
  });
});

test.describe("移动端：登录页", () => {
  test("登录页在移动端不横向滚动，表单可输入", async ({ page }) => {
    await stubApi(page);
    await page.goto("/login");
    await expect(page.getByTestId("login-page")).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);
    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("wrong-password");
    await expect(page.getByRole("button", { name: "登录" })).toBeEnabled();
  });
});
