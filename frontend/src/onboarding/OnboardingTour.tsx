/**
 * 引导挂载点 —— 挂在路由根，跨页面导航存活，自身不渲染任何 DOM（气泡与遮罩由
 * driver.js 挂到 body 上）。
 *
 * 四件事：
 * 1. 进入主界面后查一次「是否已看过」（auth 开启 = 登录成功后；匿名 = auth status 放行
 *    后，两种情形都由 `isAuthenticated` 统一表达）。登录页不掺和。
 * 2. 未看过则自动开一次。
 * 3. 开启但当前所在路由（pathname 或该步声明的查询参数）不是当前步骤所需的（如设置页
 *    点「重看引导」回到第 1 步、跨页步骤切到下一段、设置页两步间换 section）时先导航
 *    过去，锚点与内容区才能就位。
 * 4. store 里 active 为真、且当前路由是引导覆盖的路由之一时驱动 driver.js —— 自动
 *    首弹与设置页「重看引导」共用这条路径，组件本身不区分二者。
 *
 * 步骤大纲跨大厅、设置页与演示工作台（项目概览 / 角色集 / 剧集分镜三条路由）。driver.js
 * 实例挂在 `document.body` 上、在 React 树之外，只要第 3 点里的路由判定在这些页面之间
 * 保持同一个真值，实例就不会被 effect 4 拆重建——「下一步」跨页时靠 `tour.ts` 的
 * `onStepChange` 在 driver 真正切换高亮之前把即将停靠的步号同步上报，本组件据此提前
 * 导航，驱动效果本身不重启。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { APP_PROJECT_WORKSPACE_PATTERN, APP_TOP_LEVEL_ROUTES } from "@/app-routes";
import { buildTourSteps } from "./steps";
import { startTour, type TourLabels } from "./tour";

export function OnboardingTour() {
  const { t } = useTranslation("onboarding");
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [location, navigate] = useLocation();
  const search = useSearch();
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

  // 步骤大纲只随界面语言重建，渲染期与两个效果共用同一份，避免每次渲染重跑一遍翻译。
  const steps = useMemo(() => buildTourSteps(t), [t]);
  // 引导覆盖的路由集合，从大纲自身派生而非另写一份常量——后续段落在新页面加步骤时
  // 只需给那一步写 `route`，这里自动跟上，不存在「忘了同步」的维护缺口。
  const tourRoutes = useMemo(
    () => new Set(steps.map((s) => s.route).filter((route): route is string => Boolean(route))),
    [steps],
  );

  // 停在第几步（0 基）。用 ref 给驱动效果读取初始值又不把它列进依赖（步骤切换不该
  // 重启驱动效果），state 给下面的路由判定效果做响应式依赖。两者总是同步写入。
  const stepIndexRef = useRef(0);
  const [stepIndex, setStepIndex] = useState(0);
  const setStep = (index: number) => {
    stepIndexRef.current = index;
    setStepIndex(index);
  };

  // 当前所在路由是否是引导覆盖的路由之一（大厅或设置页）——驱动效果（4）以此为准，
  // 而不是逐步比对，这样大厅↔设置页之间的跨页步骤切换不会拆重建 driver 实例。
  const onTourRoute = tourRoutes.has(normalizedLocation);
  const requiredRoute = steps[stepIndex]?.route ?? null;
  const requiredQuery = steps[stepIndex]?.query;
  // 该步声明的查询参数是否已满足。同一 pathname 下按查询参数切分区的页面（设置页的
  // `section`），pathname 相同不代表内容区落对了地方——设置页两步之间就靠这个判定
  // 触发导航，把内容区从供应商切到智能体（以及退回时切回来）。
  const querySatisfied =
    !requiredQuery ||
    Object.entries(requiredQuery).every(([key, value]) => new URLSearchParams(search).get(key) === value);

  // 0. 引导开启但当前路由（pathname 或该步声明的查询参数）不是本步所需的——先导航
  //    过去，锚点与内容区才能就位。跨页步骤切换（见 tour.ts 的 onStepChange）、同页
  //    换分区与「重看引导」从非首步路由触发都走这里。
  //
  //    `interactive` 步是例外：该步的落点动作本身就是导航离开 requiredRoute（点演示卡
  //    进演示工作台），不是驱动效果（3）触发的跨页步骤切换，此时不该把用户拽回来。
  //
  //    豁免只认这一步自己声明的落点 `interactiveTarget`（含其子路由），不是「凡是离开
  //    requiredRoute 都算」。落点之外的去处一律拽回，无论它在不在引导覆盖范围内：跳到
  //    另一条引导路由（如顶栏「设置」）要拽回，跳到引导之外的主界面路由（如资产库）同样
  //    要拽回——否则 driver 停在演示卡那一步却找不到锚点，降级成与页面内容不符的居中气泡。
  //    这也让 `interactive` 步与普通步在「跑到无关页面」时表现一致，差别只在它多认一个
  //    自己声明的落点。不能改判「落点在引导覆盖范围之外」——演示工作台的路由已随工作台段
  //    进了 `tourRoutes`，那个条件会让用户刚点进工作台就被弹回大厅。
  //
  //    顺着入口走进落点时，效果（2.5）把步号推进到下一步——点卡片与点「下一步」殊途同归，
  //    引导顺势接着讲工作台段，不挂起等待。
  const currentStep = steps[stepIndex];
  const interactiveTarget = currentStep?.interactive ? currentStep.interactiveTarget : undefined;
  const enteredInteractiveTarget =
    interactiveTarget !== undefined &&
    (normalizedLocation === interactiveTarget || normalizedLocation.startsWith(`${interactiveTarget}/`));
  useEffect(() => {
    if (
      !active ||
      !inMainUi ||
      !requiredRoute ||
      (normalizedLocation === requiredRoute && querySatisfied) ||
      enteredInteractiveTarget
    )
      return;
    // 走 replace：这是引导自己的纠偏跳转，不是用户点出来的去处。设置页两步之间来回步进
    // 会反复导航，push 的话历史里会堆满引导的中间态，用户按浏览器后退得连按多次才退得
    // 出去。用户主动产生的导航（点演示卡进工作台）不经这里，仍是正常的 push。
    navigate(
      requiredQuery ? `${requiredRoute}?${new URLSearchParams(requiredQuery).toString()}` : requiredRoute,
      { replace: true },
    );
  }, [
    active,
    inMainUi,
    requiredRoute,
    requiredQuery,
    querySatisfied,
    normalizedLocation,
    navigate,
    enteredInteractiveTarget,
  ]);

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

  // 2.5 用户在 `interactive` 步顺着入口走进落点（点演示卡进演示工作台）——把步号推进
  //     到下一步，与点「下一步」殊途同归。同一帧里效果（3）先按 enteredInteractiveTarget
  //     收起旧 driver 实例；这里推进步号后 enteredInteractiveTarget 随之变假（新步不是
  //     interactive），效果（3）随即在新步号上重建实例，接着讲工作台段。
  useEffect(() => {
    if (!active || !enteredInteractiveTarget) return;
    const next = stepIndexRef.current + 1;
    if (next >= steps.length) return;
    setStep(next);
  }, [active, enteredInteractiveTarget, steps]);

  // 3. 驱动 driver.js
  //
  // 文案是构造时一次性交给 driver 的，切换界面语言（`t` 换身份）必须重建一遍才能生效。
  // 重建走 `dispose()` —— 不记退出 —— 并把停留的步号带过去，讲到第几步就还在第几步。
  useEffect(() => {
    // 离开引导覆盖的路由（如运行期间浏览器后退回登录页）时收起正在运行的引导——不算
    // 一次退出（不记 seen），保留步号，回到引导覆盖的路由后从原位继续。用户在
    // `interactive` 步顺着入口走进落点的那一帧同样先收起旧实例：效果（2.5）随即把步号
    // 推进到下一步，本效果在新步号上重建实例，外观上就是顺势接着讲。
    if (!active || !onTourRoute || enteredInteractiveTarget) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 引导退出时把步号复位到起点，是有意的受控重置，下次开启从头开始
      if (!active) setStep(0);
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
    const handle = startTour(steps, labels, {
      onExit: () => useOnboardingStore.getState().exit(),
      startIndex: stepIndexRef.current,
      onStepChange: setStep,
    });
    return () => {
      setStep(handle.currentIndex());
      handle.dispose();
    };
    // 步骤号有意不进依赖（stepIndexRef 是 ref，读取本就不受 lint 约束）——步骤号变化
    // 不该重启这个效果，否则每次「下一步」都会拆重建 driver 实例。
  }, [active, onTourRoute, enteredInteractiveTarget, steps, t]);

  return null;
}
