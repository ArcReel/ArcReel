import React from "react";
import htm from "htm";

import { ROUTE_KIND, VIEW_META } from "../constants.js";
import { cn } from "../utils.js";
import { IconAssistant, IconProject, IconSpark, IconUsage } from "./icons.js";

const html = htm.bind(React.createElement);

export function AppShell({
    route,
    dashboardKind,
    selectedProjectItem,
    projectsCount,
    totalCalls,
    onNavigate,
    onToggleAssistantPanel,
    headerActions = null,
    children,
}) {
    const fixedViewportLayout =
        route.kind === ROUTE_KIND.ASSISTANT || route.kind === ROUTE_KIND.WORKSPACE;

    return html`
        <div className="app-frame flex">
            <aside className="app-sidebar hidden md:flex md:w-72 h-screen overflow-y-auto p-5 flex-col gap-6">
                <div>
                    <button
                        onClick=${() => onNavigate({ kind: ROUTE_KIND.LANDING })}
                        className="inline-flex items-center gap-3"
                    >
                        <span className="w-9 h-9 rounded-xl bg-gradient-to-br from-neon-400 to-amberx-400"></span>
                        <div>
                            <p className="app-title text-lg font-semibold tracking-wide">漫剧工厂</p>
                            <p className="text-xs text-slate-400">AI 剧集生产后台</p>
                        </div>
                    </button>
                </div>

                <nav className="space-y-2">
                    <button
                        onClick=${() => onNavigate({ kind: ROUTE_KIND.PROJECTS })}
                        className=${cn(
                            "app-menu-item w-full rounded-xl px-3 py-2.5 flex items-center gap-3 text-sm border",
                            route.kind === ROUTE_KIND.PROJECTS || route.kind === ROUTE_KIND.WORKSPACE
                                ? "border-neon-400/40 bg-neon-500/10 text-neon-300"
                                : "border-white/10 bg-white/5 text-slate-300 hover:border-white/25"
                        )}
                    >
                        <${IconProject} />
                        <span>漫剧项目</span>
                    </button>
                    <button
                        onClick=${() => onNavigate({ kind: ROUTE_KIND.ASSISTANT })}
                        className=${cn(
                            "app-menu-item w-full rounded-xl px-3 py-2.5 flex items-center gap-3 text-sm border",
                            route.kind === ROUTE_KIND.ASSISTANT
                                ? "border-neon-400/40 bg-neon-500/10 text-neon-300"
                                : "border-white/10 bg-white/5 text-slate-300 hover:border-white/25"
                        )}
                    >
                        <${IconAssistant} />
                        <span>对话管理</span>
                    </button>
                    <button
                        onClick=${() => onNavigate({ kind: ROUTE_KIND.USAGE })}
                        className=${cn(
                            "app-menu-item w-full rounded-xl px-3 py-2.5 flex items-center gap-3 text-sm border",
                            route.kind === ROUTE_KIND.USAGE
                                ? "border-neon-400/40 bg-neon-500/10 text-neon-300"
                                : "border-white/10 bg-white/5 text-slate-300 hover:border-white/25"
                        )}
                    >
                        <${IconUsage} />
                        <span>费用统计</span>
                    </button>
                </nav>

                <div className="mt-auto space-y-3">
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                        <p className="text-xs text-slate-400">当前项目数</p>
                        <p className="mt-2 text-2xl font-semibold">${projectsCount}</p>
                    </div>
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                        <p className="text-xs text-slate-400">总调用次数</p>
                        <p className="mt-2 text-2xl font-semibold text-neon-300">${totalCalls}</p>
                    </div>
                </div>
            </aside>

            <main className="flex-1 h-screen min-h-0 p-4 md:p-7 flex flex-col gap-5 overflow-hidden">
                <header className="app-panel rounded-2xl px-4 py-4 md:px-6 flex flex-col md:flex-row md:items-center md:justify-between gap-3 shrink-0">
                    <div>
                        <h1 className="text-xl md:text-2xl font-semibold app-title">${VIEW_META[dashboardKind].label}</h1>
                        <p className="mt-1 text-sm text-slate-400">${VIEW_META[dashboardKind].description}</p>
                    </div>

                    <div className="md:hidden grid grid-cols-3 gap-2">
                        <button onClick=${() => onNavigate({ kind: ROUTE_KIND.PROJECTS })} className=${cn("h-9 rounded-lg text-xs", route.kind === ROUTE_KIND.PROJECTS || route.kind === ROUTE_KIND.WORKSPACE ? "bg-neon-500/20 text-neon-300" : "bg-white/5 text-slate-300")}>项目</button>
                        <button onClick=${() => onNavigate({ kind: ROUTE_KIND.ASSISTANT })} className=${cn("h-9 rounded-lg text-xs", route.kind === ROUTE_KIND.ASSISTANT ? "bg-neon-500/20 text-neon-300" : "bg-white/5 text-slate-300")}>对话</button>
                        <button onClick=${() => onNavigate({ kind: ROUTE_KIND.USAGE })} className=${cn("h-9 rounded-lg text-xs", route.kind === ROUTE_KIND.USAGE ? "bg-neon-500/20 text-neon-300" : "bg-white/5 text-slate-300")}>费用</button>
                    </div>

                    <div className="hidden md:flex items-center gap-2">
                        ${headerActions}
                        <span className="px-2 py-1 rounded-full bg-white/5 border border-white/10 text-xs text-slate-400">${selectedProjectItem ? `当前：${selectedProjectItem.title || selectedProjectItem.name}` : "未选择项目"}</span>
                    </div>
                </header>

                <section
                    className=${cn(
                        "flex-1 min-h-0",
                        fixedViewportLayout ? "overflow-hidden" : "overflow-y-auto"
                    )}
                >
                    ${children}
                </section>
            </main>

            <button
                onClick=${onToggleAssistantPanel}
                className="fixed right-5 bottom-5 z-50 w-14 h-14 rounded-full bg-neon-500 text-ink-950 shadow-glow hover:bg-neon-400 transition-colors flex items-center justify-center"
                title="助手工作台"
            >
                <${IconSpark} />
            </button>
        </div>
    `;
}
