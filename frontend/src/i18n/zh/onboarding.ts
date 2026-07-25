import type enOnboarding from '@/i18n/en/onboarding';

export default {
  // 引导步骤
  'welcome_title': '欢迎来到 [[brand]]',
  'welcome_body': '把一本小说交给它，它会拆出分镜、生成画面、剪成短视频。这趟引导只讲解界面，不会改动你的任何数据。',
  'lobby_create_title': '从这里新建项目',
  'lobby_create_body': '每个项目都从一本小说开始。导入 .txt、.docx、.epub 或 .pdf，[[brand]] 会把它读成一集集剧本，供你逐集推进。',
  'lobby_demo_title': '项目推进后长这样',
  'lobby_demo_body': '这是一张示例卡片，不是你的项目。角标显示当前阶段，下方计数随制作进度记录角色、场景、道具和分集的完成情况。',
  'lobby_settings_title': '供应商配置在设置里',
  'lobby_settings_body': '图像、视频、文本三类生成各自跑在你选定的供应商上。按钮上的红点表示还有必填项没配齐，进设置页会直接指出缺口。',
  'settings_providers_title': '配置一个媒体供应商',
  'settings_providers_body': '在这里至少配好一个供应商——填入 API Key 并跑一次连接测试，再开始生成。',
  'settings_agent_title': '接入 Agent',
  'settings_agent_body': '生成概述、剧本和对话回复都依赖 Anthropic 凭证，在这里添加。',
  'finish_title': '轮到你了',
  'finish_body': '从导入一本小说开始，剩下的一步步来。想再看一遍，去「设置 → 关于」打开这份引导。',

  // 引导控件
  'next': '继续',
  'prev': '上一步',
  'done': '完成',
  'skip': '跳过',
  'close': '关闭引导',
  'progress': '第 {{current}} 步，共 {{total}} 步',

  // 引导期间展示的演示卡
  'demo_section_eyebrow': '示例项目',
  'demo_section_note': '仅在引导期间展示',
  'demo_project_title': '爱丽丝梦游仙境',
  'demo_project_style': '水彩绘本',

  // 设置 → 关于 的入口
  'replay_title': '使用引导',
  'replay_desc': '重看首次使用引导。只讲解界面，不改动任何数据。',
  'replay_action': '重看引导',
} satisfies Record<keyof typeof enOnboarding, string>;
