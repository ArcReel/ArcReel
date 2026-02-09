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
    assistantPendingQuestion,
    assistantAnsweringQuestion,
    handleSendAssistantMessage,
    handleAnswerAssistantQuestion,
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
            assistantPendingQuestion=${assistantPendingQuestion}
            assistantAnsweringQuestion=${assistantAnsweringQuestion}
            onSendAssistantMessage=${handleSendAssistantMessage}
            onAnswerAssistantQuestion=${handleAnswerAssistantQuestion}
            assistantChatScrollRef=${assistantChatScrollRef}
        />
    `;
}
