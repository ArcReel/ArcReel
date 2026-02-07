import { useEffect } from "react";
import { ROUTE_KIND } from "../constants.js";

export function useDocumentTitle(routeKind, currentProjectName) {
    useEffect(() => {
        if (routeKind === ROUTE_KIND.LANDING) {
            document.title = "漫剧工厂 · 把小说批量做成漫剧视频";
            return;
        }

        if (routeKind === ROUTE_KIND.WORKSPACE && currentProjectName) {
            document.title = `${currentProjectName} · 漫剧工厂`;
            return;
        }

        if (routeKind === ROUTE_KIND.ASSISTANT) {
            document.title = "对话管理 · 漫剧工厂";
            return;
        }

        if (routeKind === ROUTE_KIND.USAGE) {
            document.title = "费用统计 · 漫剧工厂";
            return;
        }

        document.title = "漫剧项目 · 漫剧工厂";
    }, [routeKind, currentProjectName]);
}
