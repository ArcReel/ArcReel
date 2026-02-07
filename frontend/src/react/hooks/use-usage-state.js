import { useCallback, useEffect, useState } from "react";

import { ROUTE_KIND } from "../constants.js";

const USAGE_PAGE_SIZE = 20;

export function useUsageState({ routeKind, pushToast }) {
    const [usageProjects, setUsageProjects] = useState([]);
    const [usageFilters, setUsageFilters] = useState({
        projectName: "",
        callType: "",
        status: "",
        startDate: "",
        endDate: "",
    });
    const [usageStats, setUsageStats] = useState(null);
    const [usageCalls, setUsageCalls] = useState([]);
    const [usageTotal, setUsageTotal] = useState(0);
    const [usagePage, setUsagePage] = useState(1);
    const [usageLoading, setUsageLoading] = useState(false);

    const loadUsageProjects = useCallback(async () => {
        try {
            const data = await window.API.getUsageProjects();
            setUsageProjects(data.projects || []);
        } catch (error) {
            pushToast(`加载费用项目失败：${error.message}`, "error");
        }
    }, [pushToast]);

    const loadUsageData = useCallback(async () => {
        setUsageLoading(true);
        try {
            const [stats, calls] = await Promise.all([
                window.API.getUsageStats({
                    projectName: usageFilters.projectName || undefined,
                    startDate: usageFilters.startDate || undefined,
                    endDate: usageFilters.endDate || undefined,
                }),
                window.API.getUsageCalls({
                    projectName: usageFilters.projectName || undefined,
                    callType: usageFilters.callType || undefined,
                    status: usageFilters.status || undefined,
                    startDate: usageFilters.startDate || undefined,
                    endDate: usageFilters.endDate || undefined,
                    page: usagePage,
                    pageSize: USAGE_PAGE_SIZE,
                }),
            ]);

            setUsageStats(stats);
            setUsageCalls(calls.items || []);
            setUsageTotal(calls.total || 0);
        } catch (error) {
            pushToast(`加载费用数据失败：${error.message}`, "error");
        } finally {
            setUsageLoading(false);
        }
    }, [pushToast, usageFilters, usagePage]);

    useEffect(() => {
        if (routeKind !== ROUTE_KIND.USAGE) {
            return;
        }

        void loadUsageProjects();
        void loadUsageData();
    }, [routeKind, loadUsageData, loadUsageProjects]);

    return {
        usageProjects,
        usageFilters,
        setUsageFilters,
        usageStats,
        usageCalls,
        usageTotal,
        usagePage,
        setUsagePage,
        usageLoading,
        usagePageSize: USAGE_PAGE_SIZE,
    };
}
