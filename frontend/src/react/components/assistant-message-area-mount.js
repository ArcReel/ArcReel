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
    assistantInterrupting,
    assistantPendingQuestion,
    assistantAnsweringQuestion,
    sessionStatus,
    sessionStatusDetail,
    handleSendAssistantMessage,
    handleInterruptAssistantSession,
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
            assistantInterrupting=${assistantInterrupting}
            assistantPendingQuestion=${assistantPendingQuestion}
            assistantAnsweringQuestion=${assistantAnsweringQuestion}
            sessionStatus=${sessionStatus}
            sessionStatusDetail=${sessionStatusDetail}
            onSendAssistantMessage=${handleSendAssistantMessage}
            onInterruptAssistantSession=${handleInterruptAssistantSession}
            onAnswerAssistantQuestion=${handleAnswerAssistantQuestion}
            assistantChatScrollRef=${assistantChatScrollRef}
        />
    `;
}
