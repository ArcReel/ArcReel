// PROTOTYPE — 视觉方向原型（#2963），不合并。
// 三个预设 A/B/C 是七条视觉轴的组合；底栏可逐轴覆盖，方便「A 的这个 + C 的那个」式取舍。
// 轴值写到 <html data-v-*>，由 visual-proto.css 覆盖真实页面，并由样本页 /prototype/visual 集中展示。

export const AXES = {
  surface: {
    label: "弹层材质",
    values: { glass: "玻璃（渐变 + blur）", solid: "不透明 surface" },
  },
  deco: {
    label: "光晕与网格",
    values: { full: "保留（现状）", ambient: "只留页面底色渐变", none: "全部去掉" },
  },
  eyebrow: {
    label: "等宽小标题",
    values: { mono: "等宽大写（现状）", sans: "本地化常规字", none: "删除" },
  },
  brand: {
    label: "品牌紫用法",
    values: { gradient: "渐变 + 光晕（现状）", flat: "纯色主按钮", restrained: "紫只用于焦点与选中" },
  },
  danger: {
    label: "危险色",
    values: { amber: "琥珀（现状）", red: "红色 destructive" },
  },
  radius: {
    label: "圆角刻度",
    values: { tw: "Tailwind 默认（现状）", nova10: "Nova 派生 10px", nova8: "Nova 派生 8px" },
  },
  running: {
    label: "运行中",
    values: { pulse: "透明度呼吸（现状）", static: "静态点 + 计数", bar: "不确定进度条" },
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
      surface: "glass",
      deco: "full",
      eyebrow: "mono",
      brand: "gradient",
      danger: "amber",
      radius: "tw",
      running: "pulse",
    },
  },
  B: {
    name: "精修：保留氛围、收敛装饰",
    axes: {
      surface: "solid",
      deco: "ambient",
      eyebrow: "sans",
      brand: "flat",
      danger: "red",
      radius: "nova8",
      running: "static",
    },
  },
  C: {
    name: "极简：去装饰、紫色退居强调",
    axes: {
      surface: "solid",
      deco: "none",
      eyebrow: "none",
      brand: "restrained",
      danger: "red",
      radius: "nova10",
      running: "bar",
    },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];
