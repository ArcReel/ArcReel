// PROTOTYPE — 剧集页原型（#2974），不合并。
// 三个预设 A/B/C 是八条轴的组合；底栏可逐轴覆盖，方便「B 的这个 + C 的那个」式取舍。
// 工作区外壳固定为「工作区外壳与 Agent 面板」的结论（预设 B），不在这里切换。

export const AXES = {
  header: {
    label: "页头",
    values: {
      stacked: "四层叠放（现状）",
      oneRow: "单行：集头 + 三个 tab + 动作",
      twoRow: "两行：集头一行，tab 与动作一行",
    },
  },
  progress: {
    label: "制作进度",
    values: {
      inline: "原地展开、不限高（现状）",
      capped: "原地展开、限高 40% 内部滚动",
      drawer: "页头入口 + 右侧抽屉（Sheet）",
      popover: "页头入口 + 下拉面板（Popover）",
    },
  },
  editor: {
    label: "分镜栏位",
    values: {
      current: "固定 220 | 余量给提示词 | 340（现状）",
      mediaWide: "提示词限宽 640，余量给媒体栏",
      fourCol: "宽时加第四栏：分镜图、视频分栏",
    },
  },
  sizing: {
    label: "调宽方式",
    values: {
      tiers: "容器查询切 2–3 种固定布局",
      resizable: "各栏可拖拽调宽",
    },
  },
  dialogue: {
    label: "台词与发声",
    values: {
      left: "左栏（现状）",
      mid: "中栏，接在视频提示词后",
    },
  },
  shotNav: {
    label: "切换分镜",
    values: {
      blocked: "有草稿时禁止翻页（现状）",
      intercept: "三按钮拦截 + J/K 切换、⌘S 保存",
      interceptAlt: "三按钮拦截 + Alt+↑/↓ 切换、⌘S 保存",
    },
  },
  edit: {
    label: "剪辑视图",
    values: {
      current: "播放器、轨道、详情纵向排列，整页滚动（现状）",
      sideInspector: "播放器 + 右侧详情，轨道横跨底部，不滚动",
      rightRail: "播放器与轨道在左，详情占满右栏，不滚动",
    },
  },
  refCols: {
    label: "参考视频工作台",
    values: {
      current: "固定宽度，预览固定 16:9（现状）",
      aspect: "固定宽度，预览按项目画幅",
      resizable: "可拖拽调宽，预览按项目画幅",
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
      header: "stacked",
      progress: "inline",
      editor: "current",
      sizing: "tiers",
      dialogue: "left",
      shotNav: "blocked",
      edit: "current",
      refCols: "current",
    },
  },
  B: {
    name: "单行页头 + 媒体优先",
    axes: {
      header: "oneRow",
      progress: "popover",
      editor: "mediaWide",
      sizing: "tiers",
      dialogue: "mid",
      shotNav: "intercept",
      edit: "sideInspector",
      refCols: "aspect",
    },
  },
  C: {
    name: "两行页头 + 可调宽",
    axes: {
      header: "twoRow",
      progress: "drawer",
      editor: "fourCol",
      sizing: "resizable",
      dialogue: "left",
      shotNav: "interceptAlt",
      edit: "rightRail",
      refCols: "resizable",
    },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];
