import React from "react";
import htm from "htm";

import { cn } from "../utils.js";

const html = htm.bind(React.createElement);

/**
 * 任务统计条组件
 * 显示各状态任务数量的紧凑统计条
 */
export function TaskStatsBar({ stats, connected, className = "" }) {
    const { queued = 0, running = 0, succeeded = 0, failed = 0 } = stats || {};
    const total = queued + running + succeeded + failed;

    return html`
        <div className=${cn("flex items-center gap-3 text-xs", className)}>
            <span
                className=${cn(
                    "inline-flex items-center gap-1.5",
                    connected ? "text-green-400" : "text-slate-500"
                )}
            >
                <span
                    className=${cn(
                        "w-2 h-2 rounded-full",
                        connected ? "bg-green-400 animate-pulse" : "bg-slate-500"
                    )}
                />
                ${connected ? "已连接" : "离线"}
            </span>

            <span className="text-slate-500">|</span>

            <span className="text-slate-400">队列中</span>
            <span className="text-amber-400 font-medium">${queued}</span>

            <span className="text-slate-400">运行中</span>
            <span className="text-blue-400 font-medium">${running}</span>

            <span className="text-slate-400">成功</span>
            <span className="text-green-400 font-medium">${succeeded}</span>

            <span className="text-slate-400">失败</span>
            <span className="text-red-400 font-medium">${failed}</span>

            <span className="text-slate-500">|</span>
            <span className="text-slate-400">共 ${total} 个任务</span>
        </div>
    `;
}
