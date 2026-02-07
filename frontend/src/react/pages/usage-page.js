import React from "react";
import htm from "htm";

import { formatDateTime, formatDuration, formatMoney } from "../utils.js";
import { Badge, Button, Card } from "../components/primitives.js";

const html = htm.bind(React.createElement);

export function UsagePage({
    usageProjects,
    usageFilters,
    setUsageFilters,
    usageStats,
    usageCalls,
    usageTotal,
    usagePage,
    setUsagePage,
    usagePageSize,
    usageLoading,
}) {
    const totalPages = Math.max(1, Math.ceil(usageTotal / usagePageSize));

    return html`
        <div className="space-y-5">
            <${Card}>
                <div className="grid lg:grid-cols-5 md:grid-cols-3 gap-3">
                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        项目
                        <select
                            value=${usageFilters.projectName}
                            onChange=${(event) => {
                                setUsagePage(1);
                                setUsageFilters((previous) => ({ ...previous, projectName: event.target.value }));
                            }}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        >
                            <option value="">全部项目</option>
                            ${usageProjects.map((projectName) => html`
                                <option key=${projectName} value=${projectName}>${projectName}</option>
                            `)}
                        </select>
                    </label>

                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        类型
                        <select
                            value=${usageFilters.callType}
                            onChange=${(event) => {
                                setUsagePage(1);
                                setUsageFilters((previous) => ({ ...previous, callType: event.target.value }));
                            }}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        >
                            <option value="">全部</option>
                            <option value="image">图片</option>
                            <option value="video">视频</option>
                        </select>
                    </label>

                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        状态
                        <select
                            value=${usageFilters.status}
                            onChange=${(event) => {
                                setUsagePage(1);
                                setUsageFilters((previous) => ({ ...previous, status: event.target.value }));
                            }}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        >
                            <option value="">全部</option>
                            <option value="success">成功</option>
                            <option value="failed">失败</option>
                        </select>
                    </label>

                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        开始日期
                        <input
                            type="date"
                            value=${usageFilters.startDate}
                            onChange=${(event) => {
                                setUsagePage(1);
                                setUsageFilters((previous) => ({ ...previous, startDate: event.target.value }));
                            }}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        />
                    </label>

                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        结束日期
                        <input
                            type="date"
                            value=${usageFilters.endDate}
                            onChange=${(event) => {
                                setUsagePage(1);
                                setUsageFilters((previous) => ({ ...previous, endDate: event.target.value }));
                            }}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        />
                    </label>
                </div>
            <//>

            <div className="grid sm:grid-cols-2 xl:grid-cols-5 gap-3">
                <${Card} className="p-4"><p className="text-xs text-slate-400">总费用</p><p className="mt-2 text-2xl font-semibold text-neon-300">${formatMoney(usageStats?.total_cost || 0)}</p><//>
                <${Card} className="p-4"><p className="text-xs text-slate-400">图片调用</p><p className="mt-2 text-2xl font-semibold text-sky-300">${usageStats?.image_count || 0}</p><//>
                <${Card} className="p-4"><p className="text-xs text-slate-400">视频调用</p><p className="mt-2 text-2xl font-semibold text-emerald-300">${usageStats?.video_count || 0}</p><//>
                <${Card} className="p-4"><p className="text-xs text-slate-400">总调用</p><p className="mt-2 text-2xl font-semibold text-slate-100">${usageStats?.total_count || 0}</p><//>
                <${Card} className="p-4"><p className="text-xs text-slate-400">失败次数</p><p className="mt-2 text-2xl font-semibold text-red-300">${usageStats?.failed_count || 0}</p><//>
            </div>

            <${Card} className="p-0 overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm min-w-[900px]">
                        <thead className="bg-white/5 text-slate-300">
                            <tr>
                                <th className="px-4 py-3 text-left font-medium">时间</th>
                                <th className="px-4 py-3 text-left font-medium">项目</th>
                                <th className="px-4 py-3 text-left font-medium">类型</th>
                                <th className="px-4 py-3 text-left font-medium">状态</th>
                                <th className="px-4 py-3 text-left font-medium">分辨率</th>
                                <th className="px-4 py-3 text-left font-medium">时长</th>
                                <th className="px-4 py-3 text-left font-medium">耗时</th>
                                <th className="px-4 py-3 text-right font-medium">费用</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-white/10">
                            ${usageLoading
                                ? html`<tr><td colSpan="8" className="px-4 py-10 text-center text-slate-400">数据加载中...</td></tr>`
                                : usageCalls.length === 0
                                    ? html`<tr><td colSpan="8" className="px-4 py-10 text-center text-slate-400">暂无调用记录</td></tr>`
                                    : usageCalls.map((call) => html`
                                          <tr className="hover:bg-white/5">
                                              <td className="px-4 py-3 text-slate-300">${formatDateTime(call.started_at || call.created_at)}</td>
                                              <td className="px-4 py-3">${call.project_name || "-"}</td>
                                              <td className="px-4 py-3">
                                                  <${Badge}
                                                      className=${call.call_type === "video"
                                                          ? "bg-emerald-500/15 text-emerald-300 border border-emerald-400/30"
                                                          : "bg-sky-500/15 text-sky-300 border border-sky-400/30"}
                                                  >
                                                      ${call.call_type === "video" ? "视频" : "图片"}
                                                  <//>
                                              </td>
                                              <td className="px-4 py-3">
                                                  <${Badge}
                                                      className=${call.status === "success"
                                                          ? "bg-neon-500/15 text-neon-300 border border-neon-400/30"
                                                          : "bg-red-500/15 text-red-300 border border-red-400/30"}
                                                  >
                                                      ${call.status === "success" ? "成功" : call.status === "failed" ? "失败" : call.status}
                                                  <//>
                                              </td>
                                              <td className="px-4 py-3 text-slate-300">${call.resolution || "-"}</td>
                                              <td className="px-4 py-3 text-slate-300">${call.duration_seconds ? `${call.duration_seconds}s` : "-"}</td>
                                              <td className="px-4 py-3 text-slate-300">${formatDuration(call.duration_ms)}</td>
                                              <td className="px-4 py-3 text-right ${call.cost_usd > 0 ? "text-neon-300" : "text-slate-500"}">
                                                  ${call.cost_usd > 0 ? `$${call.cost_usd.toFixed(3)}` : "-"}
                                              </td>
                                          </tr>
                                      `)}
                        </tbody>
                    </table>
                </div>

                <div className="flex items-center justify-between px-4 py-3 border-t border-white/10 text-sm">
                    <p className="text-slate-400">共 ${usageTotal} 条记录</p>
                    <div className="flex items-center gap-2">
                        <${Button}
                            variant="ghost"
                            size="sm"
                            disabled=${usagePage <= 1}
                            onClick=${() => setUsagePage((previous) => Math.max(1, previous - 1))}
                        >
                            上一页
                        <//>
                        <span className="text-slate-400">第 ${usagePage} / ${totalPages} 页</span>
                        <${Button}
                            variant="ghost"
                            size="sm"
                            disabled=${usagePage >= totalPages}
                            onClick=${() => setUsagePage((previous) => Math.min(totalPages, previous + 1))}
                        >
                            下一页
                        <//>
                    </div>
                </div>
            <//>
        </div>
    `;
}
