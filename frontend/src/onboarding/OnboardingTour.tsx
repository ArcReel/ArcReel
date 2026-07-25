/**
 * 引导挂载点 —— 挂在路由根，跨页面导航存活，自身不渲染任何 DOM（气泡与遮罩由
 * driver.js 挂到 body 上）。
 *
 * 四件事：
 * 1. 进入主界面后查一次「是否已看过」（auth 开启 = 登录成功后；匿名 = auth status 放行
 *    后，两种情形都由 `isAuthenticated` 统一表达）。登录页不掺和。
 * 2. 未看过则自动开一次。
 * 3. 开启但当前不在大厅（如设置页点「重看引导」、或深链接直落其它主界面路由）时先
 *    导航到大厅——当前步骤大纲的锚点全部落在大厅，其它页面上没有可高亮的目标。
 * 4. store 里 active 为真且已在大厅时驱动 driver.js —— 自动首弹与设置页「重看引导」
 *    共用这条路径，组件本身不区分二者。
 */

import { useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { APP_PROJECT_WORKSPACE_PATTERN, APP_TOP_LEVEL_ROUTES, ROUTE_APP_PROJECTS } from "@/app-routes";
import { buildTourSteps } from "./steps";
import { startTour, type TourLabels } from "./tour";

export function OnboardingTour() {
  const { t } = useTranslation("onboarding");
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [location, navigate] = useLocation();
  const seen = useOnboardingStore((s) => s.seen);
  const active = useOnboardingStore((s) => s.active);

  // 只在已知应用路由内生效——未匹配路由（404）与 /login 一样不掺和，否则引导会
  // 在错链接 / 旧书签落地的 404 页自动弹出，且关闭时把全局 seen 标记写成已看过。
  // /app/settings、/app/assets 是无子路由的单页，前缀匹配会把 /app/settings/unknown
  // 这类 404 误判为主界面；/app/projects/:projectName 下 StudioCanvasRouter 的内层
  // <Switch> 同样没有兜底路由，未注册的子路径按 APP_PROJECT_WORKSPACE_PATTERN 精确匹配。
  // wouter 底层 regexparam 大小写不敏感、且非 loose 模式下末尾斜杠可选（pattern 以
  // `\/?$` 收尾），这里统一转小写、去掉末尾斜杠后再比对，避免大小写变体或带尾斜杠的
  // 合法路径（wouter 能正常渲染）被本判断误判为不在主界面内。
  const normalizedLocation = location.toLowerCase().replace(/(.)\/$/, "$1");
  const inMainUi =
    isAuthenticated &&
    (normalizedLocation === "/" ||
      (APP_TOP_LEVEL_ROUTES as readonly string[]).includes(normalizedLocation) ||
      APP_PROJECT_WORKSPACE_PATTERN.test(normalizedLocation));
  // 当前步骤大纲的锚点全部落在大厅（ROUTE_APP_PROJECTS）——「重看引导」在设置页
  // 等其它主界面路由触发时，driver.js 找不到锚点只会退化居中，不会自动跳转。
  // 后续段落若在设置页/工作台新增带锚点的步骤，需按步骤索引扩展这里的路由判定。
  const atLobby = normalizedLocation === ROUTE_APP_PROJECTS;

  // 0. 引导开启但不在大厅——先导航过去，锚点才能挂载
  useEffect(() => {
    if (!active || atLobby || !inMainUi) return;
    navigate(ROUTE_APP_PROJECTS);
  }, [active, atLobby, inMainUi, navigate]);

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
    // 离开主界面（如运行期间浏览器后退回登录页）或尚未导航到大厅时收起正在运行的
    // 引导——不算一次退出（不记 seen），保留步号，回到大厅后从原位继续。
    if (!active || !atLobby) {
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
  }, [active, atLobby, t]);

  return null;
}
