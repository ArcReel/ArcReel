// main.tsx — New entry point using wouter + StudioLayout
// Replaces main.js as the application entry point.
// The old main.js is kept as a reference during the migration.

import { createRoot } from "react-dom/client";
import { AppRoutes } from "./router";

import "./index.css";
import "./css/styles.css";
import "./css/app.css";
import "./css/studio.css";

const root = document.getElementById("app-root");
if (root) {
  createRoot(root).render(<AppRoutes />);
}
