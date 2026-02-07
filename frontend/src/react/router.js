import { DEFAULT_TAB, PROJECT_TABS, ROUTE_KIND } from "./constants.js";

export function normalizeProjectTab(tab) {
    return PROJECT_TABS.some((item) => item.key === tab) ? tab : DEFAULT_TAB;
}

export function parseRouteFromLocation() {
    const { pathname, search } = window.location;
    const params = new URLSearchParams(search);
    const tab = normalizeProjectTab(params.get("tab"));

    if (pathname === "/") {
        return { kind: ROUTE_KIND.LANDING };
    }

    if (pathname === "/app" || pathname === "/app/") {
        return { kind: ROUTE_KIND.PROJECTS };
    }

    if (pathname === "/app/projects") {
        return { kind: ROUTE_KIND.PROJECTS };
    }

    if (pathname.startsWith("/app/projects/")) {
        const projectName = decodeURIComponent(pathname.replace("/app/projects/", "").trim());
        if (projectName) {
            return {
                kind: ROUTE_KIND.WORKSPACE,
                projectName,
                tab,
            };
        }
        return { kind: ROUTE_KIND.PROJECTS };
    }

    if (pathname === "/app/assistant") {
        return { kind: ROUTE_KIND.ASSISTANT };
    }

    if (pathname === "/app/usage") {
        return { kind: ROUTE_KIND.USAGE };
    }

    return { kind: ROUTE_KIND.PROJECTS };
}

export function buildUrl(route) {
    if (route.kind === ROUTE_KIND.LANDING) {
        return "/";
    }

    if (route.kind === ROUTE_KIND.PROJECTS) {
        return "/app/projects";
    }

    if (route.kind === ROUTE_KIND.WORKSPACE) {
        const projectName = encodeURIComponent(route.projectName || "");
        if (!projectName) {
            return "/app/projects";
        }
        const tab = normalizeProjectTab(route.tab);
        return tab === DEFAULT_TAB
            ? `/app/projects/${projectName}`
            : `/app/projects/${projectName}?tab=${tab}`;
    }

    if (route.kind === ROUTE_KIND.ASSISTANT) {
        return "/app/assistant";
    }

    if (route.kind === ROUTE_KIND.USAGE) {
        return "/app/usage";
    }

    return "/app/projects";
}
