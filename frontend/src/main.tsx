import { createRoot } from "react-dom/client";
import { AppRoutes } from "./router";
import { useAuthStore } from "@/stores/auth-store";
import { i18nReady } from "@/i18n";
import { BRAND, BRAND_DOCUMENT_TITLE } from "@/branding";
import { TooltipProvider } from "@/components/ui/tooltip";

import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "driver.js/dist/driver.css";

import "./index.css";
// driver 默认皮肤之后加载，覆盖生效
import "./css/onboarding.css";

// 启动时按 BRAND 设置文档标题与 meta description（index.html 中的
// <title> 与 <meta name="description"> 仅作为加载阶段的占位）。
document.title = BRAND_DOCUMENT_TITLE;
const metaDescription = document.querySelector<HTMLMetaElement>('meta[name="description"]');
if (metaDescription) {
  metaDescription.content = BRAND.description;
}

// 从 localStorage 恢复登录状态
useAuthStore.getState().initialize();

const root = document.getElementById("app-root");
if (root) {
  // 等 i18n 当前语言 + fallback 的 namespace 全部加载完再渲染，避免首屏闪 key。
  // chunk 都是本地 lazy import，弱网下也只是几十 ms 延迟（cold start）。
  // i18n 加载失败时不能阻塞应用启动（仍 render，让 t() 退回 key 字符串），
  // 但失败必须可观测——所以显式记 error 而不是用 finally 把成功/失败合流静默。
  const render = () =>
    createRoot(root).render(
      <TooltipProvider>
        <AppRoutes />
      </TooltipProvider>,
    );
  i18nReady.then(render, (err) => {
    console.error("i18n initialization failed", err);
    render();
  });
}
