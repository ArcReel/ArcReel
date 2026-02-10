import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ROUTE_KIND } from "../src/react/constants.js";
import { AppShell } from "../src/react/components/app-shell.js";

function renderShell(routeKind) {
    return renderToStaticMarkup(
        React.createElement(AppShell, {
            route: { kind: routeKind, tab: "overview", projectName: "test" },
            dashboardKind: routeKind,
            selectedProjectItem: null,
            projectsCount: 1,
            totalCalls: 0,
            onNavigate: () => {},
            onToggleAssistantPanel: () => {},
            headerActions: null,
            children: React.createElement("div", null, "body"),
        })
    );
}

const assistantMarkup = renderShell(ROUTE_KIND.ASSISTANT);
assert(
    !assistantMarkup.includes('title="助手工作台"'),
    "Expected assistant route to hide floating assistant trigger button."
);

const workspaceMarkup = renderShell(ROUTE_KIND.WORKSPACE);
assert(
    workspaceMarkup.includes('title="助手工作台"'),
    "Expected non-assistant routes to keep floating assistant trigger button."
);

console.log("app-shell-floating-button.test passed");
