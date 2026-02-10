import React, { useState } from "react";
import htm from "htm";

import { cn } from "../utils.js";
import { TaskItem } from "./task-item.js";
import { TaskStatsBar } from "./task-stats-bar.js";

const html = htm.bind(React.createElement);

const TABS = [
    { key: "running", label: "进行中" },
    { key: "queued", label: "排队中" },
    { key: "completed", label: "已完成" },
];

/**
 * 任务队列面板组件
 * 显示任务列表和统计信息，支持标签页切换
 */
export function TaskQueuePanel({
    tasks,
    stats,
    connected,
    queuedTasks,
    runningTasks,
    completedTasks,
    onRefresh,
    className = "",
}) {
    const [activeTab, setActiveTab] = useState("running");

    const getTabTasks = () => {
        switch (activeTab) {
            case "running":
                return runningTasks;
            case "queued":
                return queuedTasks;
            case "completed":
                return completedTasks;
            default:
                return [];
        }
    };

    const getTabCount = (key) => {
        switch (key) {
            case "running":
                return runningTasks?.length || 0;
            case "queued":
                return queuedTasks?.length || 0;
            case "completed":
                return completedTasks?.length || 0;
            default:
                return 0;
        }
    };

    const currentTasks = getTabTasks();

    return html`
        <div
            className=${cn(
                "flex flex-col h-full bg-ink-900 rounded-xl border border-white/10",
                className
            )}
        >
            <!-- 头部 -->
            <div className="shrink-0 p-4 border-b border-white/10">
                <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-slate-200">任务队列</h3>
                    <button
                        onClick=${onRefresh}
                        className="text-xs text-slate-400 hover:text-slate-200 transition-colors"
                    >
                        刷新
                    </button>
                </div>
                <${TaskStatsBar} stats=${stats} connected=${connected} />
            </div>

            <!-- 标签页 -->
            <div className="shrink-0 flex border-b border-white/10">
                ${TABS.map(
                    (tab) => {
                        const tabCount = getTabCount(tab.key);
                        const runningBadgeAnimated = tab.key === "running" && tabCount > 0;
                        return html`
                            <button
                                key=${tab.key}
                                onClick=${() => setActiveTab(tab.key)}
                                className=${cn(
                                    "flex-1 px-3 py-2.5 text-xs font-medium transition-colors",
                                    activeTab === tab.key
                                        ? "text-neon-300 border-b-2 border-neon-400"
                                        : "text-slate-400 hover:text-slate-200"
                                )}
                            >
                                ${tab.label}
                                <span
                                    className=${cn(
                                        "ml-1.5 px-1.5 py-0.5 rounded-full text-[10px]",
                                        activeTab === tab.key
                                            ? "bg-neon-500/20 text-neon-300"
                                            : "bg-white/10 text-slate-500",
                                        runningBadgeAnimated ? "animate-pulse" : ""
                                    )}
                                >
                                    ${tabCount}
                                </span>
                            </button>
                        `;
                    }
                )}
            </div>

            <!-- 任务列表 -->
            <div className="flex-1 overflow-y-auto p-3 space-y-2">
                ${currentTasks.length === 0
                    ? html`
                          <div className="flex items-center justify-center h-24 text-xs text-slate-500">
                              暂无任务
                          </div>
                      `
                    : currentTasks.map(
                          (task) => html`
                              <${TaskItem} key=${task.task_id} task=${task} />
                          `
                      )}
            </div>
        </div>
    `;
}
