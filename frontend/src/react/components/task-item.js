import React from "react";
import htm from "htm";

import { cn } from "../utils.js";

const html = htm.bind(React.createElement);

const STATUS_CONFIG = {
    queued: {
        label: "排队中",
        className: "bg-amber-500/20 text-amber-300 border-amber-500/30",
        icon: "⏳",
    },
    running: {
        label: "生成中",
        className: "bg-blue-500/20 text-blue-300 border-blue-500/30 animate-pulse",
        icon: "🔄",
    },
    succeeded: {
        label: "成功",
        className: "bg-green-500/20 text-green-300 border-green-500/30",
        icon: "✅",
    },
    failed: {
        label: "失败",
        className: "bg-red-500/20 text-red-300 border-red-500/30",
        icon: "❌",
    },
};

const TASK_TYPE_LABELS = {
    storyboard: "分镜图",
    video: "视频",
    character: "人物图",
    clue: "线索图",
    storyboard_grid: "多宫格",
};

function formatTime(isoString) {
    if (!isoString) return "";
    try {
        const date = new Date(isoString);
        return date.toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    } catch {
        return "";
    }
}

/**
 * 单个任务项组件
 */
export function TaskItem({ task, compact = false }) {
    const statusConfig = STATUS_CONFIG[task.status] || STATUS_CONFIG.queued;
    const taskTypeLabel = TASK_TYPE_LABELS[task.task_type] || task.task_type;

    if (compact) {
        return html`
            <div
                className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/5 border border-white/10 text-xs"
            >
                <span>${statusConfig.icon}</span>
                <span className="text-slate-300 truncate flex-1">
                    ${task.resource_id || task.task_id}
                </span>
                <span className="text-slate-500">${taskTypeLabel}</span>
            </div>
        `;
    }

    return html`
        <div
            className="p-3 rounded-xl bg-white/5 border border-white/10 space-y-2"
        >
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-200 truncate">
                        ${task.resource_id || task.task_id}
                    </p>
                    <p className="text-xs text-slate-400 mt-0.5">
                        ${taskTypeLabel} · ${task.project_name}
                    </p>
                </div>
                <span
                    className=${cn(
                        "shrink-0 px-2 py-0.5 rounded-full text-[10px] border",
                        statusConfig.className
                    )}
                >
                    ${statusConfig.label}
                </span>
            </div>

            <div className="flex items-center gap-3 text-[10px] text-slate-500">
                <span>创建: ${formatTime(task.created_at)}</span>
                ${task.started_at &&
                html`<span>开始: ${formatTime(task.started_at)}</span>`}
                ${task.completed_at &&
                html`<span>完成: ${formatTime(task.completed_at)}</span>`}
            </div>

            ${task.status === "failed" &&
            task.error_message &&
            html`
                <p className="text-xs text-red-400 bg-red-500/10 rounded-lg px-2 py-1.5 mt-2">
                    ${task.error_message}
                </p>
            `}
        </div>
    `;
}
