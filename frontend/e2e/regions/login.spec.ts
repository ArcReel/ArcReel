import { defineRegionScenarios } from "../support/scenarios.ts";

defineRegionScenarios("登录页", [
  {
    name: "未登录打开登录页",
    path: "/login",
    auth: "signed-out",
    ready: async (page) => {
      await page.getByRole("button", { name: "登录" }).waitFor();
    },
  },
  {
    name: "登录失败显示错误提示",
    path: "/login",
    auth: "signed-out",
    api: { "POST /api/v1/auth/token": { status: 401, body: { detail: "用户名或密码错误" } } },
    ready: async (page) => {
      await page.getByRole("button", { name: "登录" }).waitFor();
    },
    act: async (page) => {
      await page.getByLabel("用户名").fill("demo");
      await page.getByLabel("密码").fill("wrong-password");
      await page.getByRole("button", { name: "登录" }).click();
      await page.getByRole("alert").waitFor();
    },
  },
]);
