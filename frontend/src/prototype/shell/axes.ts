// PROTOTYPE — 工作区外壳与 Agent 面板原型（#2970），不合并。
// 三个预设 A/B/C 是九条轴的组合；底栏可逐轴覆盖，方便「B 的这个 + C 的那个」式取舍。
// 紧凑档 = 视口宽度 < 1280（见「宽窗口原则与最低支持视口」结论）。

export const AXES = {
  panel: {
    label: "Agent 面板",
    values: { push: "挤压画布", overlay: "覆盖画布（非模态浮层）" },
  },
  compact: {
    label: "紧凑档 <1280",
    values: {
      keep: "与标准档相同",
      railSidebar: "侧栏收为图标栏",
      railSidebarOverlay: "侧栏收为图标栏 + Agent 改覆盖",
    },
  },
  resize: {
    label: "调宽",
    values: {
      agentOnly: "只拖 Agent（360–720，现状）",
      both: "侧栏 200–320、Agent 320–640 都可拖",
      none: "固定宽度（侧栏 256、Agent 420）",
    },
  },
  reopen: {
    label: "收起后入口",
    values: { ball: "右上悬浮球（现状）", header: "顶栏右端常驻按钮", rail: "右缘 40px 竖条" },
  },
  autoCollapse: {
    label: "单集时侧栏",
    values: { off: "保持用户选择（现状）", episode: "单集与剪辑都折叠为图标栏", edit: "仅剪辑视图折叠" },
  },
  motion: {
    label: "开合方式",
    values: {
      width300: "宽度过渡 300ms（现状）",
      instant: "瞬时切换",
      fade: "瞬时 + 内容淡入 150ms",
      slide: "Sheet 式滑入 200ms",
    },
  },
  settings: {
    label: "设置入口",
    values: {
      single: "一个齿轮，项目内指向项目设置（现状）",
      split: "拆成两个按钮",
      menu: "齿轮=全局设置，项目设置进项目菜单",
    },
  },
  projectMenu: {
    label: "项目菜单",
    values: { current: "当前项目卡片（现状）", switcher: "项目切换器", actions: "「项目操作」菜单" },
  },
  composer: {
    label: "Agent 输入框",
    values: {
      current: "只在键入时重算，上限 50vh（现状）",
      fieldSizing: "field-sizing 自动撑高，上限面板 40%",
      fixedExpand: "固定 3 行 + 展开按钮",
    },
  },
} as const;

export type AxisKey = keyof typeof AXES;
export type AxisValue<K extends AxisKey> = keyof (typeof AXES)[K]["values"];
export type AxisState = { [K in AxisKey]: AxisValue<K> };

export const AXIS_KEYS = Object.keys(AXES) as AxisKey[];

export const PRESETS: Record<"A" | "B" | "C", { name: string; axes: AxisState }> = {
  A: {
    name: "现状",
    axes: {
      panel: "push",
      compact: "keep",
      resize: "agentOnly",
      reopen: "ball",
      autoCollapse: "off",
      motion: "width300",
      settings: "single",
      projectMenu: "current",
      composer: "current",
    },
  },
  B: {
    name: "挤压 + 顶栏入口（推荐）",
    axes: {
      panel: "push",
      compact: "railSidebarOverlay",
      resize: "both",
      reopen: "header",
      autoCollapse: "episode",
      motion: "fade",
      settings: "menu",
      projectMenu: "switcher",
      composer: "fieldSizing",
    },
  },
  C: {
    name: "画布优先：覆盖式",
    axes: {
      panel: "overlay",
      compact: "railSidebar",
      resize: "agentOnly",
      reopen: "rail",
      autoCollapse: "edit",
      motion: "slide",
      settings: "split",
      projectMenu: "actions",
      composer: "fixedExpand",
    },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];

export const COMPACT_BREAKPOINT = 1280;
export const CANVAS_MIN_WIDTH = 480;
export const SIDEBAR_RAIL_WIDTH = 56;
export const AGENT_RAIL_WIDTH = 40;

export const WIDTH_LIMITS = {
  agentOnly: { sidebar: [256, 256], agent: [360, 720], agentDefault: 505 },
  both: { sidebar: [200, 320], agent: [320, 640], agentDefault: 420 },
  none: { sidebar: [256, 256], agent: [420, 420], agentDefault: 420 },
} as const;
