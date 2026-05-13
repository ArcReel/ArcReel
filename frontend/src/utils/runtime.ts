/**
 * 检测当前 SPA 是否运行在原生宿主（通过 preload 注入 `window.arcreel`）里。
 *
 * 宿主端 preload 在 `window.arcreel` 暴露一个标识对象（含
 * `platform === "desktop"` 与 `os`）；浏览器直接访问的部署不加载该 preload，
 * `window.arcreel` 始终是 undefined，因此存在性检查即可区分两种运行环境。
 *
 * 用法：
 *   if (isDesktop()) { ... }                       // 显隐 / 布局差异化
 *   const cmd = isMac() ? "⌘" : "Ctrl";             // 快捷键标签
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
  return typeof window !== "undefined" && window.arcreel?.os === "darwin";
}

export function isWindows(): boolean {
  return typeof window !== "undefined" && window.arcreel?.os === "win32";
}
