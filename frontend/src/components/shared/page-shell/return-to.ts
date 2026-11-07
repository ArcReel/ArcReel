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

/** Back returns to prior pages; navigation among these pages preserves return targets. */
const RETURN_TO_PAGES = [ROUTE_APP_SETTINGS, ROUTE_APP_ASSETS];

/** Redirect-only entries never retain visits; return targets would immediately redirect back. */
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
 * Record latest visited application pages including queries for global-settings/asset-library Back. Mounted at route roots,
 * so entry buttons/deep links/onboarding navigation need not each record origins. sessionStorage preserves records across page refreshes,
 * without crossing tabs.
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
 * Return to application pages visited before current pages; without records (e.g. direct URLs), return to project lobbies.
 * For top-level pages only: nested-route `navigate` resolves relative to nested paths.
 */
export function useReturnTo() {
  const [, navigate] = useLocation();
  return useCallback(() => {
    const target = sessionStorage.getItem(RETURN_TO_KEY);
    navigate(target && isAppPath(target) ? target : ROUTE_APP_PROJECTS);
  }, [navigate]);
}
