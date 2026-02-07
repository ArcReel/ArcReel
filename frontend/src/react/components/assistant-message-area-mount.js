import React from "react";
import htm from "htm";

import { AssistantMessageArea } from "../pages/assistant-page.js";

const html = htm.bind(React.createElement);

export function AssistantMessageAreaMount({
    assistantCurrentSessionId,
    assistantSessions,
    assistantMessagesLoading,
    assistantComposedMessages,
    assistantError,
    assistantSkills,
    assistantSkillsLoading,
    assistantInput,
    setAssistantInput,
    assistantSending,
    handleSendAssistantMessage,
    assistantChatScrollRef,
}) {
    return html`
        <${AssistantMessageArea}
            assistantCurrentSessionId=${assistantCurrentSessionId}
            assistantSessions=${assistantSessions}
            assistantMessagesLoading=${assistantMessagesLoading}
            assistantComposedMessages=${assistantComposedMessages}
            assistantError=${assistantError}
            assistantSkills=${assistantSkills}
            assistantSkillsLoading=${assistantSkillsLoading}
            assistantInput=${assistantInput}
            setAssistantInput=${setAssistantInput}
            assistantSending=${assistantSending}
            onSendAssistantMessage=${handleSendAssistantMessage}
            assistantChatScrollRef=${assistantChatScrollRef}
        />
    `;
}
