// PROTOTYPE — 原文与分集视图原型（#2982），不合并。
// 预设 A/B/C/D 是各条轴的组合；底栏可逐轴覆盖，方便「B 的这个 + C 的那个」式取舍。
// 工作区外壳固定为「工作区外壳与 Agent 面板」的结论，剧集页固定为「剧集页」结论（预设 D）。

export const AXES = {
  layout: {
    label: "分集视图结构",
    scope: "episodes",
    values: {
      split: "原文 + 右栏（现状结构）",
      outline: "左侧集目录 + 原文，右栏只在有事要办时出现",
      tabs: "上下 tab：原文 | 分集清单（表格）",
    },
  },
  sidebar: {
    label: "工作区侧栏",
    scope: "episodes",
    values: {
      keep: "保持展开，侧栏与画布各有一份集清单（现状）",
      collapse: "进入分集视图时折叠为图标栏，集清单只在画布里出现一次",
    },
  },
  tools: {
    label: "上传与规划入口",
    scope: "episodes",
    values: {
      rail: "右栏顶部（现状）",
      header: "页头一行：进度、新建一集、上传原文、AI 规划分集",
    },
  },
  list: {
    label: "集清单密度",
    scope: "episodes",
    values: {
      full: "每集都展开钩子与首尾句（现状）",
      compact: "单行，选中后才展开首尾句与操作",
    },
  },
  notices: {
    label: "未登记文件",
    scope: "episodes",
    values: {
      panel: "右栏面板（现状）",
      banner: "页头下方提示条，点开对话框处理",
    },
  },
  mock: {
    label: "演示状态",
    scope: "episodes",
    values: {
      none: "真实数据",
      replan: "模拟一份「新的分集方案」（只在内存里）",
    },
  },
  review: {
    label: "集页：原文审阅",
    scope: "episode",
    values: {
      standalone: "独立页，没有 tab（现状）",
      planTab: "并入「脚本规划」tab 的起步态",
      sourceTab: "新增「原文」tab，「脚本规划」tab 放起步区",
    },
  },
  planEntry: {
    label: "集页：AI 规划脚本",
    scope: "episode",
    values: {
      dialog: "对话框（现状）",
      inline: "内嵌在起步区：附加要求 + 两个按钮",
    },
  },
  guide: {
    label: "集页：本集导览",
    scope: "episode",
    values: {
      top: "原文上方，可折叠（现状）",
      side: "原文右侧栏，宽窗口用上余量",
    },
  },
} as const;

export type AxisKey = keyof typeof AXES;
export type AxisValue<K extends AxisKey> = keyof (typeof AXES)[K]["values"];
export type AxisState = { [K in AxisKey]: AxisValue<K> };

export const AXIS_KEYS = Object.keys(AXES) as AxisKey[];

export const PRESETS: Record<"A" | "B" | "C" | "D", { name: string; axes: Omit<AxisState, "mock"> }> = {
  A: {
    name: "现状",
    axes: {
      layout: "split",
      sidebar: "keep",
      tools: "rail",
      list: "full",
      notices: "panel",
      review: "standalone",
      planEntry: "dialog",
      guide: "top",
    },
  },
  B: {
    name: "精修双栏",
    axes: {
      layout: "split",
      sidebar: "keep",
      tools: "header",
      list: "compact",
      notices: "banner",
      review: "planTab",
      planEntry: "dialog",
      guide: "side",
    },
  },
  C: {
    name: "目录导航",
    axes: {
      layout: "outline",
      sidebar: "collapse",
      tools: "header",
      list: "compact",
      notices: "banner",
      review: "sourceTab",
      planEntry: "inline",
      guide: "side",
    },
  },
  D: {
    name: "上下 tab",
    axes: {
      layout: "tabs",
      sidebar: "keep",
      tools: "header",
      list: "compact",
      notices: "banner",
      review: "planTab",
      planEntry: "inline",
      guide: "top",
    },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];
