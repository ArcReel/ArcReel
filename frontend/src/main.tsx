// main.tsx — New entry point using wouter + StudioLayout
// Replaces main.js as the application entry point.
// The old main.js is kept as a reference during the migration.

import { createRoot } from "react-dom/client";
import { AppRoutes } from "./router";
import { useAuthStore } from "@/stores/auth-store";
import { i18nReady } from "@/i18n";
import { usePermissionsStore } from "@/stores/fork-permissions-store"; // fork-private
import { BRAND, BRAND_DOCUMENT_TITLE } from "@/branding";

import "./index.css";
import "./css/styles.css";
import "./css/app.css";
import "./css/studio.css";

// 启动时按 BRAND 设置文档标题与 meta description（index.html 中的
// <title> 与 <meta name="description"> 仅作为加载阶段的占位）。
document.title = BRAND_DOCUMENT_TITLE;
const metaDescription = document.querySelector<HTMLMetaElement>('meta[name="description"]');
if (metaDescription) {
  metaDescription.content = BRAND.description;
}

// 从 localStorage 恢复登录状态
useAuthStore.getState().initialize();

// fork-private: 拉取当前用户角色（如已登录），失败也不阻塞渲染
void usePermissionsStore.getState().fetchMe();

// ---------------------------------------------------------------------------
// 全局滚动条 auto-hide：滚动时渐显、停止 1.2s 后渐隐
// ---------------------------------------------------------------------------
{
  const timers = new WeakMap<Element, ReturnType<typeof setTimeout>>();

  document.addEventListener(
    "scroll",
    (e) => {
      const el = e.target;
      if (!(el instanceof HTMLElement)) return;

      // 显示滚动条
      el.dataset.scrolling = "";

      // 清除上一次的隐藏定时器
      const prev = timers.get(el);
      if (prev) clearTimeout(prev);

      // 1.2s 无滚动后隐藏
      timers.set(
        el,
        setTimeout(() => {
          delete el.dataset.scrolling;
          timers.delete(el);
        }, 1200),
      );
    },
    true, // capture phase — 捕获所有子元素的 scroll 事件
  );
}

const root = document.getElementById("app-root");
if (root) {
  // 等 i18n 当前语言 + fallback 的 namespace 全部加载完再渲染，避免首屏闪 key。
  // chunk 都是本地 lazy import，弱网下也只是几十 ms 延迟（cold start）。
  // i18n 加载失败时不能阻塞应用启动（仍 render，让 t() 退回 key 字符串），
  // 但失败必须可观测——所以显式记 error 而不是用 finally 把成功/失败合流静默。
  const render = () => createRoot(root).render(<AppRoutes />);
  i18nReady.then(render, (err) => {
    console.error("i18n initialization failed", err);
    render();
  });
}
