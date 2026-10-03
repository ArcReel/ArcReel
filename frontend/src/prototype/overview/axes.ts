// PROTOTYPE — 项目概览页与空项目欢迎页原型（#2981），不合并。
// 预设 A–D 是各条轴的组合，E 是维护者的选择；底栏可逐轴覆盖，方便「B 的这个 + C 的那个」式取舍。
// 工作区外壳固定为「工作区外壳与 Agent 面板」的结论，剧集页固定为「剧集页」的结论（预设 D）。

export const AXES = {
  sample: {
    label: "样本状态",
    values: {
      real: "真实数据",
      empty: "空项目（没有概述、没有集）",
      adNew: "刚建好的广告项目（无商品、无灵感）",
    },
  },
  layout: {
    label: "版式与宽度",
    values: {
      legacy: "现状组件（忽略其余轴）",
      bible: "单列设定页，限宽 760 靠左",
      dashboard: "两栏：设定 + 右侧「制作」栏，容器窄时堆叠",
      episodes: "集优先：设定摘要 + 集卡网格铺满",
    },
  },
  header: {
    label: "页头",
    values: {
      title: "大标题 + 模式（现状）",
      meta: "标题 + 元信息行（模式 · 画幅 · 风格 · 集数）",
      none: "不设页头，项目名只在顶栏",
    },
  },
  fields: {
    label: "故事设定的编辑",
    values: {
      editMode: "「编辑」按钮进入表单（现状）",
      boxed: "字段常驻输入框 + 内联未保存提示条",
      seamless: "正文样式，悬停/聚焦显出边框 + 内联未保存提示条",
    },
  },
  episodeList: {
    label: "集清单",
    values: {
      costRows: "逐集一行，带费用明细（现状）",
      compact: "精简行：序号、标题、进度条、时长",
      cards: "集卡网格：分镜图 / 视频进度",
      none: "删除（侧栏、顶栏逐集清单已有）",
    },
  },
  assets: {
    label: "资产完成度",
    values: {
      bars: "三张进度卡（现状）",
      inline: "一行计数，点击进画廊",
      none: "删除（侧栏已有计数）",
    },
  },
  cost: {
    label: "费用",
    values: {
      full: "预估 / 实际两列明细（现状）",
      summary: "一行摘要，明细在弹层",
      none: "删除，只留「使用记录」链接",
    },
  },
  empty: {
    label: "空项目",
    values: {
      welcome: "独立欢迎页：拖放区 + 说明（现状精简）",
      start: "开始页：上传原文 / 新建空白集 / 交给 Agent 三个入口",
      inline: "不设欢迎页：概览各区块以空状态呈现",
    },
  },
  transition: {
    label: "首次分析",
    values: {
      swap: "欢迎页内显示分析中 → 完成，再整页换成概览（现状）",
      inPlace: "上传后立即切到概览，设定区骨架占位、就地填入",
    },
  },
  adInit: {
    label: "广告初始化",
    values: {
      form: "独立初始化表单（现状精简）",
      inline: "不设初始化页：创作灵感区 + 商品区空状态",
    },
  },
  adStory: {
    label: "广告的故事设定",
    values: {
      show: "概览显示（现状）",
      tab: "概览不显示，视频页加「故事设定」tab",
    },
  },
  adDuration: {
    label: "广告目标时长",
    values: {
      number: "数字输入",
      tiers: "档位选择器 15 / 30 / 60 / 90 秒 + 自定义",
    },
  },
  notFound: {
    label: "项目内未知路径",
    values: {
      blank: "空白画布（现状）",
      empty: "画布内空状态 +「回到概览」",
      redirect: "重定向到概览 + 提示",
    },
  },
} as const;

export type AxisKey = keyof typeof AXES;
export type AxisValue<K extends AxisKey> = keyof (typeof AXES)[K]["values"];
export type AxisState = { [K in AxisKey]: AxisValue<K> };

export const AXIS_KEYS = Object.keys(AXES) as AxisKey[];

export const PRESETS: Record<"A" | "B" | "C" | "D" | "E", { name: string; axes: AxisState }> = {
  A: {
    name: "现状",
    axes: {
      sample: "real",
      layout: "legacy",
      header: "title",
      fields: "editMode",
      episodeList: "costRows",
      assets: "bars",
      cost: "full",
      empty: "welcome",
      transition: "swap",
      adInit: "form",
      adStory: "show",
      adDuration: "number",
      notFound: "blank",
    },
  },
  B: {
    name: "设定页",
    axes: {
      sample: "real",
      layout: "bible",
      header: "meta",
      fields: "seamless",
      episodeList: "none",
      assets: "inline",
      cost: "summary",
      empty: "inline",
      transition: "inPlace",
      adInit: "inline",
      adStory: "show",
      adDuration: "number",
      notFound: "empty",
    },
  },
  C: {
    name: "两栏仪表盘",
    axes: {
      sample: "real",
      layout: "dashboard",
      header: "meta",
      fields: "boxed",
      episodeList: "compact",
      assets: "bars",
      cost: "summary",
      empty: "welcome",
      transition: "inPlace",
      adInit: "form",
      adStory: "show",
      adDuration: "number",
      notFound: "empty",
    },
  },
  D: {
    name: "集优先",
    axes: {
      sample: "real",
      layout: "episodes",
      header: "none",
      fields: "seamless",
      episodeList: "cards",
      assets: "inline",
      cost: "summary",
      empty: "start",
      transition: "swap",
      adInit: "form",
      adStory: "show",
      adDuration: "number",
      notFound: "redirect",
    },
  },
  E: {
    name: "维护者选定",
    axes: {
      sample: "real",
      layout: "bible",
      header: "meta",
      fields: "seamless",
      episodeList: "none",
      assets: "inline",
      cost: "summary",
      empty: "welcome",
      transition: "inPlace",
      adInit: "inline",
      adStory: "tab",
      adDuration: "tiers",
      notFound: "empty",
    },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];
