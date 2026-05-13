/**
 * 检测当前 SPA 是否运行在 ArcReel 桌面客户端（Electron + preload）里。
 *
 * 桌面壳 `src/preload/index.ts` 在 `window.arcreel` 暴露一个标识对象，Web /
 * Docker 部署不加载该 preload，window.arcreel 始终是 undefined，因此存在性
 * 检查即可区分两种运行环境。
 *
 * 用法：
 *   if (isDesktop()) { ... }                  // 隐藏 / 显示 / 布局差异化
 *   <button hidden={isDesktop()}>下载</button>  // Web 显示、桌面隐藏
 *   const cmd = isMac() ? "⌘" : "Ctrl";        // 快捷键标签
 */

interface ArcreelClientApi {
  readonly platform: "desktop";
  readonly os: string;
}

declare global {
  interface Window {
    arcreel?: ArcreelClientApi & Record<string, unknown>;
  }
}

export function isDesktop(): boolean {
  return typeof window !== "undefined" && window.arcreel?.platform === "desktop";
}

export function isMac(): boolean {
  return window.arcreel?.os === "darwin";
}

export function isWindows(): boolean {
  return window.arcreel?.os === "win32";
}
