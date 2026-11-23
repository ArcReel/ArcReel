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

const RETURN_TO_PAGES = [ROUTE_APP_SETTINGS, ROUTE_APP_ASSETS];

const REDIRECT_ONLY_PATHS = [`${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}/${WORKSPACE_ROUTE_SETTINGS}`];

function isAppPath(path: string) {
  return path.startsWith(`${ROUTE_APP}/`);
}

function isReturnTarget(path: string) {
  const normalized = path.toLowerCase().replace(/\/+$/, "");
  if (REDIRECT_ONLY_PATHS.includes(normalized)) return false;
  return !RETURN_TO_PAGES.some((page) => normalized === page || normalized.startsWith(`${page}/`));
}

export function useTrackReturnTo() {
  const [location] = useLocation();
  const search = useSearch();
  useEffect(() => {
    if (!isAppPath(location) || !isReturnTarget(location)) return;
    sessionStorage.setItem(RETURN_TO_KEY, search ? `${location}?${search}` : location);
  }, [location, search]);
}

export function useReturnTo() {
  const [, navigate] = useLocation();
  return useCallback(() => {
    const target = sessionStorage.getItem(RETURN_TO_KEY);
    navigate(target && isAppPath(target) ? target : ROUTE_APP_PROJECTS);
  }, [navigate]);
}
