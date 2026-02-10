import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatMessage } from "../src/react/components/chat/ChatMessage.js";

function renderMessage(message) {
    return renderToStaticMarkup(React.createElement(ChatMessage, { message }));
}

test("standalone tool_result with object content should not crash", () => {
    const message = {
        type: "assistant",
        content: [
            {
                type: "tool_result",
                tool_use_id: "tool-1",
                content: { type: "text", text: "hello object result" },
            },
        ],
    };

    const markup = renderMessage(message);
    assert.ok(markup.includes("hello object result"));
});

test("standalone tool_result with array content should not crash", () => {
    const message = {
        type: "assistant",
        content: [
            {
                type: "tool_result",
                tool_use_id: "tool-2",
                content: [
                    { type: "text", text: "line A" },
                    { type: "text", text: "line B" },
                ],
            },
        ],
    };

    const markup = renderMessage(message);
    assert.ok(markup.includes("line A"));
    assert.ok(markup.includes("line B"));
});
