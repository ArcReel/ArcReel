import { useCallback, useEffect, useMemo, useState } from "react";

import { ROUTE_KIND } from "../constants.js";
import { buildUrl, normalizeProjectTab, parseRouteFromLocation } from "../router.js";

export function useAppRouter() {
    const initialRoute = useMemo(() => parseRouteFromLocation(), []);
    const [route, setRoute] = useState(initialRoute);
    const [projectTab, setProjectTab] = useState(normalizeProjectTab(initialRoute.tab));
    const [selectedProject, setSelectedProject] = useState(initialRoute.projectName || "");

    const navigate = useCallback((nextRoute, options = {}) => {
        const normalized = { ...nextRoute };
        if (normalized.kind === ROUTE_KIND.WORKSPACE) {
            normalized.tab = normalizeProjectTab(normalized.tab || projectTab);
        }

        setRoute(normalized);
        if (normalized.kind === ROUTE_KIND.WORKSPACE) {
            setProjectTab(normalized.tab);
            setSelectedProject(normalized.projectName || "");
        }

        const url = buildUrl(normalized);
        if (options.replace) {
            window.history.replaceState({}, "", url);
        } else {
            window.history.pushState({}, "", url);
        }
    }, [projectTab]);

    useEffect(() => {
        const handlePopState = () => {
            const parsed = parseRouteFromLocation();
            setRoute(parsed);
            if (parsed.kind === ROUTE_KIND.WORKSPACE) {
                setProjectTab(normalizeProjectTab(parsed.tab));
                setSelectedProject(parsed.projectName || "");
            }
        };

        window.addEventListener("popstate", handlePopState);
        return () => window.removeEventListener("popstate", handlePopState);
    }, []);

    return {
        initialProjectName: initialRoute.projectName || "",
        route,
        projectTab,
        setProjectTab,
        selectedProject,
        setSelectedProject,
        navigate,
    };
}
