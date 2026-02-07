export const ROUTE_KIND = {
    LANDING: "landing",
    PROJECTS: "projects",
    WORKSPACE: "workspace",
    ASSISTANT: "assistant",
    USAGE: "usage",
};

export const DEFAULT_TAB = "overview";

export const PROJECT_TABS = [
    { key: "overview", label: "概览" },
    { key: "tasks", label: "人物" },
    { key: "clues", label: "线索" },
    { key: "episodes", label: "剧集/场景" },
];

export const VIEW_META = {
    [ROUTE_KIND.PROJECTS]: {
        label: "漫剧项目",
        description: "按项目管理概览、任务、线索与剧集场景。",
    },
    [ROUTE_KIND.WORKSPACE]: {
        label: "项目工作台",
        description: "每个漫剧项目都在这里完成全流程协作。",
    },
    [ROUTE_KIND.ASSISTANT]: {
        label: "对话管理",
        description: "集中管理助手会话，统一查看技能调用记录。",
    },
    [ROUTE_KIND.USAGE]: {
        label: "费用统计",
        description: "按项目和时间维度追踪图片/视频调用成本。",
    },
};

export const PHASE_LABELS = {
    script: "剧本阶段",
    characters: "人物阶段",
    clues: "线索阶段",
    storyboard: "分镜阶段",
    video: "视频阶段",
    compose: "后期阶段",
    completed: "已完成",
    empty: "未开始",
    unknown: "未知",
    error: "异常",
};

export const PHASE_BADGE_CLASS = {
    script: "bg-amberx-500/15 text-amberx-400 border border-amberx-400/30",
    characters: "bg-neon-500/15 text-neon-300 border border-neon-400/30",
    clues: "bg-cyan-500/15 text-cyan-300 border border-cyan-400/30",
    storyboard: "bg-sky-500/15 text-sky-300 border border-sky-400/30",
    video: "bg-emerald-500/15 text-emerald-300 border border-emerald-400/30",
    compose: "bg-orange-500/15 text-orange-300 border border-orange-400/30",
    completed: "bg-green-500/15 text-green-300 border border-green-400/30",
    empty: "bg-slate-500/15 text-slate-300 border border-slate-400/30",
    unknown: "bg-slate-500/15 text-slate-300 border border-slate-400/30",
    error: "bg-red-500/15 text-red-300 border border-red-400/30",
};
