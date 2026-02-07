import React from "react";
import htm from "htm";

import { PROJECT_TABS } from "../constants.js";
import { cn, progressPercent } from "../utils.js";
import { Badge, Button, Card, EmptyState } from "../components/primitives.js";

const html = htm.bind(React.createElement);

function ProjectOverview({ currentProjectData }) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="未加载项目"
                description="请选择项目后查看概览。"
            />
        `;
    }

    const overview = currentProjectData.overview || {};
    const progress = currentProjectData.status?.progress || {};
    const stats = [
        { label: "人物", data: progress.characters || { total: 0, completed: 0 }, color: "bg-neon-400" },
        { label: "线索", data: progress.clues || { total: 0, completed: 0 }, color: "bg-cyan-400" },
        { label: "分镜", data: progress.storyboards || { total: 0, completed: 0 }, color: "bg-sky-400" },
        { label: "视频", data: progress.videos || { total: 0, completed: 0 }, color: "bg-emerald-400" },
    ];

    return html`
        <div className="space-y-5">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                ${stats.map((item) => {
                    const completed = item.data.completed || 0;
                    const total = item.data.total || 0;
                    const percent = progressPercent(completed, total);

                    return html`
                        <article className="rounded-xl border border-white/10 bg-white/5 p-4">
                            <p className="text-sm text-slate-400">${item.label}</p>
                            <p className="mt-2 text-2xl font-semibold">${completed}/${total}</p>
                            <div className="mt-3 h-1.5 rounded-full bg-white/10 overflow-hidden">
                                <div className=${cn("h-full rounded-full", item.color)} style=${{ width: `${percent}%` }}></div>
                            </div>
                        </article>
                    `;
                })}
            </div>

            <article className="rounded-2xl border border-white/10 bg-white/5 p-5">
                <h3 className="text-lg font-semibold">剧情概述</h3>
                <p className="mt-3 text-sm text-slate-300 leading-7 whitespace-pre-wrap">${overview.synopsis || "暂无剧情概述"}</p>
                <div className="mt-4 flex flex-wrap gap-3 text-xs text-slate-300">
                    <span className="px-2.5 py-1 rounded-full bg-white/10">类型：${overview.genre || "未设置"}</span>
                    <span className="px-2.5 py-1 rounded-full bg-white/10">主题：${overview.theme || "未设置"}</span>
                    <span className="px-2.5 py-1 rounded-full bg-white/10">世界观：${overview.world_setting || "未设置"}</span>
                </div>
            </article>
        </div>
    `;
}

function ProjectTasks({ currentProjectData }) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="暂无任务数据"
                description="项目加载后会显示任务看板。"
            />
        `;
    }

    const progress = currentProjectData.status?.progress || {};
    const episodes = currentProjectData.episodes || [];

    const tasks = [
        {
            title: "剧本准备",
            detail: episodes.length > 0 ? `已生成 ${episodes.length} 集` : "尚未生成剧集",
            done: episodes.length > 0,
        },
        {
            title: "人物素材",
            detail: `${progress.characters?.completed || 0}/${progress.characters?.total || 0}`,
            done: (progress.characters?.completed || 0) > 0,
        },
        {
            title: "线索素材",
            detail: `${progress.clues?.completed || 0}/${progress.clues?.total || 0}`,
            done: (progress.clues?.completed || 0) > 0,
        },
        {
            title: "分镜与视频",
            detail: `分镜 ${progress.storyboards?.completed || 0}/${progress.storyboards?.total || 0} · 视频 ${progress.videos?.completed || 0}/${progress.videos?.total || 0}`,
            done: (progress.videos?.completed || 0) > 0,
        },
    ];

    return html`
        <div className="space-y-5">
            <div className="grid md:grid-cols-2 gap-4">
                ${tasks.map(
                    (task) => html`
                        <article className="rounded-xl border border-white/10 bg-white/5 p-4">
                            <div className="flex items-center justify-between">
                                <h3 className="font-semibold">${task.title}</h3>
                                <${Badge}
                                    className=${task.done
                                        ? "bg-neon-500/15 text-neon-300 border border-neon-400/40"
                                        : "bg-slate-500/15 text-slate-300 border border-slate-400/30"}
                                >
                                    ${task.done ? "进行中/已完成" : "待开始"}
                                <//>
                            </div>
                            <p className="mt-3 text-sm text-slate-300">${task.detail}</p>
                        </article>
                    `
                )}
            </div>
        </div>
    `;
}

function ProjectClues({ currentProjectData }) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="暂无线索数据"
                description="请选择项目后查看线索。"
            />
        `;
    }

    const clues = Object.entries(currentProjectData.clues || {});

    if (clues.length === 0) {
        return html`
            <${EmptyState}
                title="暂无线索"
                description="可在项目流程中新增线索，保持跨场景一致性。"
            />
        `;
    }

    return html`
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
            ${clues.map(([name, clue]) => html`
                <article className="rounded-xl border border-white/10 bg-white/5 p-4">
                    <div className="flex items-center justify-between gap-3">
                        <h3 className="font-semibold truncate">${name}</h3>
                        <${Badge} className="bg-cyan-500/15 text-cyan-300 border border-cyan-400/30">${clue.type || "unknown"}<//>
                    </div>
                    <p className="mt-3 text-sm text-slate-300 line-clamp-3">${clue.description || "暂无描述"}</p>
                    <div className="mt-4 text-xs text-slate-400">重要度：${clue.importance === "major" ? "主要" : "次要"}</div>
                </article>
            `)}
        </div>
    `;
}

function ProjectEpisodes({ currentProjectData, currentScripts }) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="暂无剧集数据"
                description="请选择项目后查看剧集/场景。"
            />
        `;
    }

    const episodes = currentProjectData.episodes || [];

    if (episodes.length === 0) {
        return html`
            <${EmptyState}
                title="还没有剧集"
                description="系统生成剧本后会自动显示剧集与场景。"
            />
        `;
    }

    return html`
        <div className="space-y-3">
            ${episodes.map((episode) => {
                const scriptFile = episode.script_file?.replace("scripts/", "") || "";
                const script = currentScripts[scriptFile] || {};
                const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
                const itemCount = isNarration
                    ? (script.segments || []).length
                    : (script.scenes || []).length;

                return html`
                    <article className="rounded-xl border border-white/10 bg-white/5 p-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <h3 className="font-semibold">E${episode.episode} · ${episode.title || `第 ${episode.episode} 集`}</h3>
                                <p className="text-xs text-slate-400 mt-1">${scriptFile || "无剧本文件"}</p>
                            </div>
                            <${Badge} className="bg-white/10 border border-white/15 text-slate-200">${isNarration ? "说书模式" : "剧集动画"}<//>
                        </div>
                        <div className="mt-3 text-sm text-slate-300">${isNarration ? "片段" : "场景"}数量：${itemCount}</div>
                    </article>
                `;
            })}
        </div>
    `;
}

function WorkspaceTabContent({ projectTab, projectDetailLoading, currentProjectData, currentScripts }) {
    if (projectDetailLoading) {
        return html`<div className="py-12 text-center text-slate-400">项目数据加载中...</div>`;
    }

    if (projectTab === "overview") {
        return html`<${ProjectOverview} currentProjectData=${currentProjectData} />`;
    }

    if (projectTab === "tasks") {
        return html`<${ProjectTasks} currentProjectData=${currentProjectData} />`;
    }

    if (projectTab === "clues") {
        return html`<${ProjectClues} currentProjectData=${currentProjectData} />`;
    }

    return html`<${ProjectEpisodes} currentProjectData=${currentProjectData} currentScripts=${currentScripts} />`;
}

export function WorkspacePage({
    currentProjectData,
    currentProjectName,
    projectTab,
    onChangeProjectTab,
    onRefreshProject,
    onDeleteProject,
    projectDetailLoading,
    currentScripts,
}) {
    return html`
        <div className="h-full min-h-0">
            <${Card} className="h-full min-h-0 flex flex-col gap-5 overflow-hidden">
                <header className="flex flex-col md:flex-row md:items-end md:justify-between gap-4 shrink-0">
                    <div>
                        <h2 className="text-2xl font-semibold app-title">${currentProjectData?.title || currentProjectName || "项目"}</h2>
                        <p className="mt-1 text-sm text-slate-400">
                            ${currentProjectData?.style || "未设置视觉风格"} ·
                            ${currentProjectData?.content_mode === "narration" ? "说书+画面模式" : "剧集动画模式"}
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <${Button} variant="outline" onClick=${onRefreshProject}>刷新项目<//>
                        <${Button} variant="danger" onClick=${onDeleteProject}>删除项目<//>
                    </div>
                </header>

                <nav className="grid grid-cols-2 md:grid-cols-4 gap-2 shrink-0">
                    ${PROJECT_TABS.map((tab) => html`
                        <button
                            key=${tab.key}
                            onClick=${() => onChangeProjectTab(tab.key)}
                            className=${cn(
                                "h-10 rounded-xl text-sm transition-colors",
                                projectTab === tab.key
                                    ? "bg-neon-500/20 text-neon-300 border border-neon-400/30"
                                    : "bg-white/5 border border-white/10 text-slate-300 hover:border-white/25"
                            )}
                        >
                            ${tab.label}
                        </button>
                    `)}
                </nav>

                <div className="flex-1 min-h-0 overflow-y-auto pr-1">
                    <${WorkspaceTabContent}
                        projectTab=${projectTab}
                        projectDetailLoading=${projectDetailLoading}
                        currentProjectData=${currentProjectData}
                        currentScripts=${currentScripts}
                    />
                </div>
            <//>
        </div>
    `;
}
