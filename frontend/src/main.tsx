import { createRoot } from "react-dom/client";
import { AppRoutes } from "./router";
import { useAuthStore } from "@/stores/auth-store";
import { i18nReady } from "@/i18n";
import { BRAND, BRAND_DOCUMENT_TITLE } from "@/branding";
import { TooltipProvider } from "@/components/ui/tooltip";
import { initTheme } from "@/theme";

import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "driver.js/dist/driver.css";

import "./index.css";
// Load after driver defaults so skin overrides apply.
import "./css/onboarding.css";

// Initialize BRAND document titles/meta descriptions; index.html
// <title>/<meta name="description"> are loading placeholders only.
document.title = BRAND_DOCUMENT_TITLE;
const metaDescription = document.querySelector<HTMLMetaElement>('meta[name="description"]');
if (metaDescription) {
  metaDescription.content = BRAND.description;
}

initTheme();

// Restore authentication from localStorage.
useAuthStore.getState().initialize();

const root = document.getElementById("app-root");
if (root) {
  // Wait for current/fallback i18n namespaces, preventing initial key flashes.
  // Local lazy chunks add only tens of milliseconds under slow networks at cold start.
  // Failed i18n must not block startup; render with t() key fallback,
  // explicitly logging failures instead of silently merging success/failure through finally.
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
