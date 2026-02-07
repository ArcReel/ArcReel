import React from "react";
import htm from "htm";

import { ROUTE_KIND } from "../constants.js";
import { AssistantFloatingPanel } from "../pages/assistant-page.js";

const html = htm.bind(React.createElement);

export function AssistantFloatingMount({
    open,
    onClose,
    onNavigate,
    currentAssistantProject,
    projects,
    onAssistantScopeChange,
    assistantCurrentSessionId,
    setAssistantCurrentSessionId,
    assistantSessions,
    onCreateSession,
    messageArea,
}) {
    if (!open) {
        return null;
    }

    return html`
        <${AssistantFloatingPanel}
            onOpenManage=${() => {
                onNavigate({ kind: ROUTE_KIND.ASSISTANT });
                onClose();
            }}
            onClose=${onClose}
            currentAssistantProject=${currentAssistantProject}
            projects=${projects}
            onAssistantScopeChange=${onAssistantScopeChange}
            assistantCurrentSessionId=${assistantCurrentSessionId}
            setAssistantCurrentSessionId=${setAssistantCurrentSessionId}
            assistantSessions=${assistantSessions}
            onCreateSession=${onCreateSession}
            messageArea=${messageArea}
        />
    `;
}
