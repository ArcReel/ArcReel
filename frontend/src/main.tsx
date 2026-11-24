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
// driver 默认皮肤之后加载，覆盖生效
import "./css/onboarding.css";

document.title = BRAND_DOCUMENT_TITLE;
const metaDescription = document.querySelector<HTMLMetaElement>('meta[name="description"]');
if (metaDescription) {
  metaDescription.content = BRAND.description;
}

initTheme();

useAuthStore.getState().initialize();

const root = document.getElementById("app-root");
if (root) {
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
