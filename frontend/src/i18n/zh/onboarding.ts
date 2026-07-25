import type enOnboarding from '../en/onboarding';

export default {
  // 引导步骤
  'welcome_title': '欢迎来到 [[brand]]',
  'welcome_body': '把一本小说交给它，它会拆出分镜、生成画面、剪成短视频。这趟引导只讲解界面，不会改动你的任何数据。',
  'finish_title': '轮到你了',
  'finish_body': '从导入一本小说开始，剩下的一步步来。想再看一遍，去「设置 → 关于」打开这份引导。',

  // 引导控件
  'next': '继续',
  'prev': '上一步',
  'done': '完成',
  'skip': '跳过',
  'close': '关闭引导',
  'progress': '第 {{current}} 步，共 {{total}} 步',

  // 设置 → 关于 的入口
  'replay_title': '使用引导',
  'replay_desc': '重看首次使用引导。只讲解界面，不改动任何数据。',
  'replay_action': '重看引导',
} satisfies Record<keyof typeof enOnboarding, string>;
