/**
 * 引导挂载点 —— 挂在路由根，跨页面导航存活，自身不渲染任何 DOM（气泡与遮罩由
 * driver.js 挂到 body 上）。
 *
 * 三件事：
 * 1. 进入主界面后查一次「是否已看过」（auth 开启 = 登录成功后；匿名 = auth status 放行
 *    后，两种情形都由 `isAuthenticated` 统一表达）。登录页不掺和。
 * 2. 未看过则自动开一次。
 * 3. store 里 active 为真时驱动 driver.js —— 自动首弹与设置页「重看引导」共用这条路径，
 *    组件本身不区分二者。
 */

import { useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { APP_PROJECT_WORKSPACE_PATTERN, APP_TOP_LEVEL_ROUTES } from "@/app-routes";
import { buildTourSteps } from "./steps";
import { startTour, type TourLabels } from "./tour";

export function OnboardingTour() {
  const { t } = useTranslation("onboarding");
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [location] = useLocation();
  const seen = useOnboardingStore((s) => s.seen);
  const active = useOnboardingStore((s) => s.active);

  // 只在已知应用路由内生效——未匹配路由（404）与 /login 一样不掺和，否则引导会
  // 在错链接 / 旧书签落地的 404 页自动弹出，且关闭时把全局 seen 标记写成已看过。
  // /app/settings、/app/assets 是无子路由的单页，前缀匹配会把 /app/settings/unknown
  // 这类 404 误判为主界面；/app/projects/:projectName 下 StudioCanvasRouter 的内层
  // <Switch> 同样没有兜底路由，未注册的子路径按 APP_PROJECT_WORKSPACE_PATTERN 精确匹配。
  const inMainUi =
    isAuthenticated &&
    (location === "/" ||
      (APP_TOP_LEVEL_ROUTES as readonly string[]).includes(location) ||
      APP_PROJECT_WORKSPACE_PATTERN.test(location));

  // 1. 查询「是否已看过」
  useEffect(() => {
    if (!inMainUi) return;
    const controller = new AbortController();
    void useOnboardingStore.getState().loadStatus({ signal: controller.signal });
    return () => controller.abort();
  }, [inMainUi]);

  // 2. 未看过 → 自动开一次（退出时 seen 置真，不会再开）
  useEffect(() => {
    if (!inMainUi || seen !== false) return;
    useOnboardingStore.getState().start();
  }, [inMainUi, seen]);

  // 3. 驱动 driver.js
  //
  // 文案是构造时一次性交给 driver 的，切换界面语言（`t` 换身份）必须重建一遍才能生效。
  // 重建走 `dispose()` —— 不记退出 —— 并把停留的步号带过去，讲到第几步就还在第几步。
  const stepIndexRef = useRef(0);
  useEffect(() => {
    // 离开主界面（如运行期间浏览器后退回登录页）时收起正在运行的引导——不算一次
    // 退出（不记 seen），保留步号，回到主界面后从原位继续。
    if (!active || !inMainUi) {
      if (!active) stepIndexRef.current = 0;
      return;
    }
    const labels: TourLabels = {
      next: t("next"),
      prev: t("prev"),
      done: t("done"),
      skip: t("skip"),
      close: t("close"),
      progress: (current, total) => t("progress", { current, total }),
    };
    const handle = startTour(buildTourSteps(t), labels, {
      onExit: () => useOnboardingStore.getState().exit(),
      startIndex: stepIndexRef.current,
    });
    return () => {
      stepIndexRef.current = handle.currentIndex();
      handle.dispose();
    };
  }, [active, inMainUi, t]);

  return null;
}
