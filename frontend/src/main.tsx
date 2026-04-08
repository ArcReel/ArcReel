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

// Restore login state from localStorage
useAuthStore.getState().initialize();

// ---------------------------------------------------------------------------
// Global scrollbar auto-hide: fades in on scroll, fades out 1.2s after stop
// ---------------------------------------------------------------------------
{
  const timers = new WeakMap<Element, ReturnType<typeof setTimeout>>();

  document.addEventListener(
    "scroll",
    (e) => {
      const el = e.target;
      if (!(el instanceof HTMLElement)) return;

      // Show scrollbar
      el.dataset.scrolling = "";

      // Clear previous hide timer
      const prev = timers.get(el);
      if (prev) clearTimeout(prev);

      // Hide after 1.2s of no scrolling
      timers.set(
        el,
        setTimeout(() => {
          delete el.dataset.scrolling;
          timers.delete(el);
        }, 1200),
      );
    },
    true, // capture phase — captures scroll events from all child elements
  );
}

const root = document.getElementById("app-root");
if (root) {
  createRoot(root).render(<AppRoutes />);
}
