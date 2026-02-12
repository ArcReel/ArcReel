import test from "node:test";
import assert from "node:assert/strict";

import { composeAssistantTurnsWithDraft } from "../src/react/hooks/use-assistant-state.js";

test("composeAssistantTurnsWithDraft should dedupe duplicate tool_use id from draft and merge fields", () => {
    const turns = [
        { type: "user", content: [{ type: "text", text: "run it" }] },
        {
            type: "assistant",
            content: [
                { type: "text", text: "running..." },
                {
                    type: "tool_use",
                    id: "tool-1",
                    name: "Bash",
                    input: { command: "echo hi" },
                },
            ],
        },
    ];

    const draftTurn = {
        type: "assistant",
        content: [
            {
                type: "tool_use",
                id: "tool-1",
                name: "Bash",
                input: { timeout: 600000 },
                result: "hi",
                is_error: false,
            },
            { type: "text", text: "done" },
        ],
    };

    const composed = composeAssistantTurnsWithDraft(turns, draftTurn);
    assert.equal(composed.length, 2);

    const assistant = composed[1];
    const toolBlocks = assistant.content.filter((block) => block.type === "tool_use");
    assert.equal(toolBlocks.length, 1);
    assert.equal(toolBlocks[0].id, "tool-1");
    assert.equal(toolBlocks[0].result, "hi");
    assert.equal(toolBlocks[0].input.command, "echo hi");
    assert.equal(toolBlocks[0].input.timeout, 600000);
    assert.equal(
        assistant.content.some((block) => block.type === "text" && block.text === "done"),
        true
    );
});

test("composeAssistantTurnsWithDraft should ignore duplicate-only draft when id already committed", () => {
    const turns = [
        {
            type: "assistant",
            content: [
                {
                    type: "tool_use",
                    id: "tool-2",
                    name: "Bash",
                    input: { command: "pwd" },
                    result: "/tmp",
                },
            ],
        },
        { type: "user", content: [{ type: "text", text: "next" }] },
    ];

    const draftTurn = {
        type: "assistant",
        content: [
            {
                type: "tool_use",
                id: "tool-2",
                name: "Bash",
                input: {},
            },
        ],
    };

    const composed = composeAssistantTurnsWithDraft(turns, draftTurn);
    assert.deepEqual(composed, turns);
});

test("composeAssistantTurnsWithDraft should drop duplicated text and tool_use when draft overlaps tail", () => {
    const repeatedText = "看起来用的是 Vertex AI 后端，RPM 限制 15。让我等 5 分钟后逐个生成。";
    const turns = [
        { type: "user", content: [{ type: "text", text: "生成1-5的分镜，批量" }] },
        {
            type: "assistant",
            content: [
                { type: "text", text: repeatedText },
                {
                    type: "tool_use",
                    id: "toolu_vrtx_011kKMDvPvrbjHq75TrwPgeQ",
                    name: "Bash",
                    input: {
                        command: "echo \"等待 5 分钟让 API 配额恢复...\"",
                    },
                },
            ],
        },
    ];

    const draftTurn = {
        type: "assistant",
        content: [
            { type: "text", text: repeatedText },
            {
                type: "tool_use",
                id: "toolu_vrtx_011kKMDvPvrbjHq75TrwPgeQ",
                name: "Bash",
                input: {
                    command: "echo \"等待 5 分钟让 API 配额恢复...\"",
                    timeout: 600000,
                },
            },
        ],
    };

    const composed = composeAssistantTurnsWithDraft(turns, draftTurn);
    const assistant = composed[1];
    const textBlocks = assistant.content.filter((block) => block.type === "text" && block.text === repeatedText);
    const toolBlocks = assistant.content.filter(
        (block) => block.type === "tool_use" && block.id === "toolu_vrtx_011kKMDvPvrbjHq75TrwPgeQ"
    );

    assert.equal(textBlocks.length, 1);
    assert.equal(toolBlocks.length, 1);
    assert.equal(toolBlocks[0].input.timeout, 600000);
});

test("composeAssistantTurnsWithDraft should keep repeated leading text when overlap has no tool_use", () => {
    const turns = [
        {
            type: "assistant",
            content: [
                { type: "text", text: "OK" },
            ],
        },
    ];

    const draftTurn = {
        type: "assistant",
        content: [
            { type: "text", text: "OK" },
            { type: "text", text: "next" },
        ],
    };

    const composed = composeAssistantTurnsWithDraft(turns, draftTurn);

    assert.deepEqual(composed[0].content, [
        { type: "text", text: "OK" },
        { type: "text", text: "OK" },
        { type: "text", text: "next" },
    ]);
});
