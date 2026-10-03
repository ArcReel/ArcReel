import { useCallback, useEffect } from "react";
import { useLocation, useSearch } from "wouter";

import {
  ROUTE_APP,
  ROUTE_APP_ASSETS,
  ROUTE_APP_PROJECTS,
  ROUTE_APP_SETTINGS,
  WORKSPACE_ROUTE_SETTINGS,
} from "@/app-routes";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";

const RETURN_TO_KEY = "arcreel:returnTo";

/** 「返回」回到进入之前位置的页面。在这些页面之间往来不改变返回目标。 */
const RETURN_TO_PAGES = [ROUTE_APP_SETTINGS, ROUTE_APP_ASSETS];

/** 只做重定向、不会停留的入口。记成返回目标的话，返回后又被重定向回来。 */
const REDIRECT_ONLY_PATHS = [`${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}/${WORKSPACE_ROUTE_SETTINGS}`];

function isAppPath(path: string) {
  return path.startsWith(`${ROUTE_APP}/`);
}

function isReturnTarget(path: string) {
  const normalized = path.toLowerCase().replace(/\/+$/, "");
  if (REDIRECT_ONLY_PATHS.includes(normalized)) return false;
  return !RETURN_TO_PAGES.some((page) => normalized === page || normalized.startsWith(`${page}/`));
}

/**
 * 记录最近停留的应用页面（含查询串），供全局设置与资产库的「返回」使用。挂在路由根部，
 * 入口按钮、深链与新手引导的跳转都不需要各自记录来源。记录放在 sessionStorage，刷新页面后仍然有效，
 * 不跨标签页。
 */
export function useTrackReturnTo() {
  const [location] = useLocation();
  const search = useSearch();
  useEffect(() => {
    if (!isAppPath(location) || !isReturnTarget(location)) return;
    sessionStorage.setItem(RETURN_TO_KEY, search ? `${location}?${search}` : location);
  }, [location, search]);
}

/**
 * 返回进入当前页面之前停留的应用页面；没有记录时（如直接打开地址）回到项目大厅。
 * 只用于顶层路由上的页面：嵌套路由里的 `navigate` 以嵌套路径为基准。
 */
export function useReturnTo() {
  const [, navigate] = useLocation();
  return useCallback(() => {
    const target = sessionStorage.getItem(RETURN_TO_KEY);
    navigate(target && isAppPath(target) ? target : ROUTE_APP_PROJECTS);
  }, [navigate]);
}
