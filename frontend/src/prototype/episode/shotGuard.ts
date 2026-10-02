// PROTOTYPE — 剧集页原型（#2974）：当前分镜详情的草稿状态，供切换分镜时拦截。
// 正式实现应由编辑单元的草稿 hook 统一提供，不用模块级变量。

export interface ShotGuard {
  dirty: boolean;
  save: () => Promise<boolean>;
  discard: () => void;
}

let current: ShotGuard | null = null;

export function registerShotGuard(guard: ShotGuard | null) {
  current = guard;
}

export function getShotGuard(): ShotGuard | null {
  return current;
}
