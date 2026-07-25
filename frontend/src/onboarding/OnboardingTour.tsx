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

import { useEffect } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { buildTourSteps } from "./steps";
import { startTour, type TourLabels } from "./tour";

export function OnboardingTour() {
  const { t } = useTranslation("onboarding");
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [location] = useLocation();
  const seen = useOnboardingStore((s) => s.seen);
  const active = useOnboardingStore((s) => s.active);

  const inMainUi = isAuthenticated && location !== "/login";

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
  useEffect(() => {
    if (!active) return;
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
    });
    return () => handle.dispose();
  }, [active, t]);

  return null;
}
