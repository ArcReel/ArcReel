// main.tsx — New entry point using wouter + StudioLayout
// Replaces main.js as the application entry point.
// The old main.js is kept as a reference during the migration.

import { createRoot } from "react-dom/client";
import { AppRoutes } from "./router";
import { useAuthStore } from "@/stores/auth-store";

import "./index.css";
import "./css/styles.css";
import "./css/app.css";
import "./css/studio.css";

// 从 localStorage 恢复登录状态
useAuthStore.getState().initialize();

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
  createRoot(root).render(<AppRoutes />);
}
