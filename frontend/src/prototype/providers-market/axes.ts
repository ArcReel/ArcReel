// PROTOTYPE — 供应商、调用端点与市场页面原型（#2972），不合并，只在 prototype/2972-providers-market 分支上。
// 「三个预设 A/B/C，同一组路由 /app/settings?section=providers|endpoints|market，?variant= 切换；
//  底栏可逐轴覆盖（?p.<轴>=<值>），方便『B 的模型表 + C 的市场』式取舍。」
// 外壳固定为「页面容器档位与外壳契约」选定的变体 D；供应商与端点是全出血档，市场是铺满档。

export const AXES = {
  rail: {
    label: "二级栏",
    values: {
      fixed: "固定 240，单行",
      adaptive: "标准档 264 两行；紧凑档收为 56 图标栏",
    },
  },
  models: {
    label: "模型列表",
    values: {
      cards: "卡片（修正对齐）",
      table: "表格 + 行内展开编辑",
      inspector: "表格 + 右侧检查器（窄时 Sheet）",
    },
  },
  endpoint: {
    label: "管理端点",
    values: {
      jump: "跳到「调用端点」，带返回",
      sheet: "Sheet 就地打开",
      peek: "Popover 速览 + 跳转",
    },
  },
  cred: {
    label: "密钥增改",
    values: {
      dialog: "Dialog，立即生效；保存栏只管高级配置",
      draft: "行内编辑进草稿，保存栏统一提交",
    },
  },
  market: {
    label: "市场排布",
    values: {
      tabs: "顶部 Tabs：浏览 / 我的分享 / 设置",
      aside: "浏览为主 + 右侧边栏（宽窗口）",
      rail: "左侧筛选栏，分享与设置是栏内入口",
    },
  },
} as const;

export type AxisKey = keyof typeof AXES;
export type AxisValue<K extends AxisKey> = keyof (typeof AXES)[K]["values"];
export type AxisState = { [K in AxisKey]: AxisValue<K> };
export const AXIS_KEYS = Object.keys(AXES) as AxisKey[];

export const PRESETS: Record<"A" | "B" | "C", { name: string; axes: AxisState }> = {
  A: {
    name: "保守：卡片 + 跳转 + Tabs",
    axes: { rail: "fixed", models: "cards", endpoint: "jump", cred: "dialog", market: "tabs" },
  },
  B: {
    name: "表格：行内展开 + Sheet + 侧边栏",
    axes: { rail: "adaptive", models: "table", endpoint: "sheet", cred: "dialog", market: "aside" },
  },
  C: {
    name: "检查器：三栏 + 速览 + 筛选栏",
    axes: { rail: "fixed", models: "inspector", endpoint: "peek", cred: "draft", market: "rail" },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];
