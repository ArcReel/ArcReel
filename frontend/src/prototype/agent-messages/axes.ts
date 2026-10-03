// PROTOTYPE — Agent 面板消息区原型（#2980），不合并。
// 三个预设 A/B/C 是九条轴的组合；底栏可逐轴覆盖，方便「B 的这个 + C 的那个」式取舍。
// 外壳固定为「工作区外壳与 Agent 面板」结论（预设 B）。

export const AXES = {
  scroll: {
    label: "滚动跟随",
    values: {
      current: "轮数变化时跳到底（现状）",
      follow: "贴底跟随流式输出，上翻即停 + 跳到最新",
      anchor: "发送后把本轮提问顶到视口顶部，回复在下方展开",
    },
  },
  layout: {
    label: "消息形态",
    values: {
      current: "全部气泡 + 角色眉题（现状）",
      chat: "用户气泡靠右，Agent 正文无气泡",
      transcript: "全部靠左，用户消息为左竖线引用块",
    },
  },
  work: {
    label: "工具调用",
    values: {
      current: "各类各自样式（现状）",
      rows: "统一为单行，可逐条展开",
      grouped: "连续工序合并为一组「N 个步骤」",
    },
  },
  thinking: {
    label: "思考块",
    values: {
      current: "首行摘要斜体（现状）",
      label: "只显示「思考过程」，点开看全文",
    },
  },
  todo: {
    label: "待办列表",
    values: {
      dock: "输入框上方展开的清单（现状）",
      dockCompact: "输入框上方单行进度，点开看清单",
      inline: "随消息流，在最新一次待办更新处展开",
    },
  },
  question: {
    label: "提问向导",
    values: {
      current: "输入框上方的向导（现状）",
      composer: "Questionnaire 占用输入框位置",
      inline: "Questionnaire 作为消息流末尾的卡片",
    },
  },
  session: {
    label: "会话切换",
    values: {
      current: "顶栏小下拉 + 新建按钮（现状）",
      switcher: "顶栏标题即会话切换器（可搜索）",
      history: "顶栏「历史」按钮切到会话列表视图",
    },
  },
  composer: {
    label: "上下文与附件",
    values: {
      current: "顶部上下文横幅 + 输入框上方缩略图（现状）",
      inside: "上下文与图片都作为附件块放进输入框",
    },
  },
  failure: {
    label: "失败卡片",
    values: {
      current: "整张大卡，常显原始信息（现状）",
      compact: "一句话结论 + 操作，原始信息折叠",
    },
  },
} as const;

export type AxisKey = keyof typeof AXES;
export type AxisValue<K extends AxisKey> = keyof (typeof AXES)[K]["values"];
export type AxisState = { [K in AxisKey]: AxisValue<K> };

export const AXIS_KEYS = Object.keys(AXES) as AxisKey[];

export const PRESETS: Record<"A" | "B" | "C" | "D", { name: string; axes: AxisState }> = {
  A: {
    name: "现状",
    axes: {
      scroll: "current",
      layout: "current",
      work: "current",
      thinking: "current",
      todo: "dock",
      question: "current",
      session: "current",
      composer: "current",
      failure: "current",
    },
  },
  B: {
    name: "对话式",
    axes: {
      scroll: "follow",
      layout: "chat",
      work: "rows",
      thinking: "current",
      todo: "dockCompact",
      question: "composer",
      session: "switcher",
      composer: "inside",
      failure: "compact",
    },
  },
  C: {
    name: "记录式",
    axes: {
      scroll: "anchor",
      layout: "transcript",
      work: "grouped",
      thinking: "label",
      todo: "inline",
      question: "inline",
      session: "history",
      composer: "inside",
      failure: "compact",
    },
  },
  D: {
    name: "维护者选定",
    axes: {
      scroll: "follow",
      layout: "chat",
      work: "rows",
      thinking: "label",
      todo: "dockCompact",
      question: "composer",
      session: "history",
      composer: "inside",
      failure: "compact",
    },
  },
};

export type PresetKey = keyof typeof PRESETS;
export const PRESET_KEYS = Object.keys(PRESETS) as PresetKey[];

export const WIDTH_STOPS = [320, 420, 520, 640] as const;
