/**
 * Message grouping utilities for unifying SSE streaming and history loading.
 *
 * This module groups raw messages into "turns" for consistent UI rendering.
 * A turn represents a complete interaction unit:
 * - User turn: user's input message
 * - Assistant turn: all assistant content blocks with attached tool results
 *
 * The same grouping logic is applied on the backend for history,
 * but this module handles incremental updates from SSE streaming.
 */

// Skill content detection patterns
const SKILL_PATTERNS = [
    /^Base directory for this skill:/,
    /^Skill content:/,
    /\.claude\/skills\/.*SKILL\.md/,
];

/**
 * Check if text content is system-injected skill content.
 */
function isSkillContentText(text) {
    if (!text || typeof text !== "string") return false;
    const trimmed = text.trim();
    return SKILL_PATTERNS.some(pattern => pattern.test(trimmed));
}

/**
 * Check if a user message is system-injected (tool_result or skill content).
 */
function isSystemInjectedUserMessage(content) {
    if (typeof content === "string") {
        return isSkillContentText(content);
    }

    if (Array.isArray(content)) {
        // All blocks must be system-injected
        for (const block of content) {
            if (!block || typeof block !== "object") continue;

            const blockType = block.type || "";
            if (blockType === "tool_result") continue;
            if (blockType === "text") {
                if (isSkillContentText(block.text)) continue;
                return false; // Real user text
            }
            return false; // Unknown block type, assume user
        }
        return true;
    }

    return false;
}

/**
 * Normalize content to array of content blocks.
 */
function normalizeContent(content) {
    if (typeof content === "string") {
        const trimmed = content.trim();
        if (!trimmed) return [];
        return [{ type: "text", text: content }];
    }
    if (Array.isArray(content)) {
        return [...content];
    }
    return [];
}

/**
 * Attach tool_result to its corresponding tool_use block.
 */
function attachToolResult(block, turnContent, toolUseMap) {
    const toolUseId = block.tool_use_id;
    if (toolUseId && toolUseMap.has(toolUseId)) {
        const toolUseBlock = toolUseMap.get(toolUseId);
        toolUseBlock.result = block.content || "";
        toolUseBlock.is_error = block.is_error || false;
        return true;
    }
    return false;
}

/**
 * Attach skill content to the most recent Skill tool_use block.
 */
function attachSkillContent(text, turnContent) {
    for (let i = turnContent.length - 1; i >= 0; i--) {
        const block = turnContent[i];
        if (
            block &&
            typeof block === "object" &&
            block.type === "tool_use" &&
            block.name === "Skill"
        ) {
            block.skill_content = text;
            return true;
        }
    }
    return false;
}

/**
 * Create a new turn object.
 */
function createTurn(type, content, uuid, timestamp) {
    return {
        type,
        content: normalizeContent(content),
        uuid,
        timestamp,
    };
}

/**
 * Group raw messages into conversation turns.
 *
 * This is the incremental version that can process a stream of messages.
 * It returns an object with:
 * - turns: the grouped turns array
 * - state: internal state for incremental updates
 *
 * @param {Array} rawMessages - Array of raw messages to group
 * @param {Object} [initialState] - Previous state for incremental updates
 * @returns {{ turns: Array, state: Object }}
 */
export function groupMessagesIntoTurns(rawMessages, initialState = null) {
    if (!rawMessages || rawMessages.length === 0) {
        return { turns: [], state: { currentTurn: null, toolUseMap: new Map() } };
    }

    const turns = [];
    let currentTurn = initialState?.currentTurn || null;
    const toolUseMap = new Map(initialState?.toolUseMap || []);

    for (const msg of rawMessages) {
        const msgType = msg.type || msg.role || "";
        const content = msg.content || "";

        if (msgType === "result") {
            // Flush current turn and add result
            if (currentTurn) {
                turns.push(currentTurn);
                currentTurn = null;
            }
            turns.push({
                type: "result",
                subtype: msg.subtype || "",
                uuid: msg.uuid,
                timestamp: msg.timestamp,
            });
            continue;
        }

        if (msgType === "user") {
            if (isSystemInjectedUserMessage(content)) {
                // Attach to current assistant turn
                if (currentTurn && currentTurn.type === "assistant") {
                    const blocks = normalizeContent(content);
                    for (const block of blocks) {
                        if (!block || typeof block !== "object") continue;

                        if (block.type === "tool_result") {
                            if (!attachToolResult(block, currentTurn.content, toolUseMap)) {
                                currentTurn.content.push(block);
                            }
                        } else if (block.type === "text" && isSkillContentText(block.text)) {
                            if (!attachSkillContent(block.text, currentTurn.content)) {
                                currentTurn.content.push({ type: "skill_content", text: block.text });
                            }
                        } else {
                            currentTurn.content.push(block);
                        }
                    }
                } else if (!currentTurn) {
                    // Orphaned system message
                    currentTurn = createTurn("system", content, msg.uuid, msg.timestamp);
                }
            } else {
                // Real user message
                if (currentTurn) {
                    turns.push(currentTurn);
                }
                currentTurn = createTurn("user", content, msg.uuid, msg.timestamp);
            }
            continue;
        }

        if (msgType === "assistant") {
            const newBlocks = normalizeContent(content);

            // Track tool_use blocks for pairing
            for (const block of newBlocks) {
                if (block && block.type === "tool_use" && block.id) {
                    toolUseMap.set(block.id, block);
                }
            }

            if (currentTurn && currentTurn.type === "assistant") {
                // Merge into existing assistant turn
                currentTurn.content.push(...newBlocks);
            } else {
                // Start new assistant turn
                if (currentTurn) {
                    turns.push(currentTurn);
                }
                currentTurn = createTurn("assistant", newBlocks, msg.uuid, msg.timestamp);
                // Re-register tool blocks in new turn
                for (const block of currentTurn.content) {
                    if (block && block.type === "tool_use" && block.id) {
                        toolUseMap.set(block.id, block);
                    }
                }
            }
        }
    }

    // Return current state for incremental updates
    return {
        turns,
        currentTurn,
        state: { currentTurn, toolUseMap },
    };
}

/**
 * Merge a new message into existing turns (for SSE streaming).
 *
 * This is optimized for appending a single message to existing turns.
 *
 * @param {Array} existingTurns - Current turns array
 * @param {Object} newMessage - New message to merge
 * @param {Object} state - Current grouping state
 * @returns {{ turns: Array, state: Object }}
 */
export function mergeMessageIntoTurns(existingTurns, newMessage, state = null) {
    // For now, just re-process all messages
    // This is a simple but potentially slow approach
    // Can be optimized later if needed
    const allMessages = turnsToRawMessages(existingTurns);
    allMessages.push(newMessage);
    return groupMessagesIntoTurns(allMessages);
}

/**
 * Convert turns back to raw messages (for re-processing).
 * This is a lossy conversion used only for incremental updates.
 */
function turnsToRawMessages(turns) {
    const messages = [];
    for (const turn of turns) {
        if (!turn) continue;

        if (turn.type === "result") {
            messages.push({
                type: "result",
                subtype: turn.subtype,
                uuid: turn.uuid,
                timestamp: turn.timestamp,
            });
        } else {
            messages.push({
                type: turn.type,
                content: turn.content,
                uuid: turn.uuid,
                timestamp: turn.timestamp,
            });
        }
    }
    return messages;
}

/**
 * Finalize turns by pushing any pending currentTurn.
 */
export function finalizeTurns(turns, currentTurn) {
    const result = [...turns];
    if (currentTurn) {
        result.push(currentTurn);
    }
    return result;
}
