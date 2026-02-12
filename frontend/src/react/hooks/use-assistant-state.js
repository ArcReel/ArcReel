import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ROUTE_KIND } from "../constants.js";

const VALID_SESSION_STATUSES = new Set(["idle", "running", "completed", "error", "interrupted"]);
const TERMINAL_SESSION_STATUSES = new Set(["completed", "error", "interrupted"]);

function parseSsePayload(event) {
    if (!event || typeof event.data !== "string" || !event.data) {
        return {};
    }
    try {
        return JSON.parse(event.data);
    } catch {
        return {};
    }
}

function applyTurnPatch(previousTurns, patch) {
    const current = Array.isArray(previousTurns) ? previousTurns : [];
    if (!patch || typeof patch !== "object") {
        return current;
    }

    const op = patch.op;
    if (op === "reset") {
        return Array.isArray(patch.turns) ? patch.turns : [];
    }
    if (op === "append") {
        if (!patch.turn || typeof patch.turn !== "object") {
            return current;
        }
        return [...current, patch.turn];
    }
    if (op === "replace_last") {
        if (!patch.turn || typeof patch.turn !== "object") {
            return current;
        }
        if (current.length === 0) {
            return [patch.turn];
        }
        return [...current.slice(0, -1), patch.turn];
    }

    return current;
}

function normalizeTurn(turn) {
    if (!turn || typeof turn !== "object") {
        return null;
    }
    const type = typeof turn.type === "string" ? turn.type : "";
    if (!type) {
        return null;
    }
    const content = Array.isArray(turn.content) ? turn.content : [];
    return {
        ...turn,
        type,
        content,
    };
}

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function mergeToolUseBlocks(committedBlock, draftBlock) {
    const committed = isRecord(committedBlock) ? committedBlock : {};
    const draft = isRecord(draftBlock) ? draftBlock : {};
    const merged = {
        ...committed,
        ...draft,
    };

    const committedHasInput = Object.prototype.hasOwnProperty.call(committed, "input");
    const draftHasInput = Object.prototype.hasOwnProperty.call(draft, "input");
    if (isRecord(committed.input) && isRecord(draft.input)) {
        merged.input = { ...committed.input, ...draft.input };
    } else if (!draftHasInput && committedHasInput) {
        merged.input = committed.input;
    }

    if (
        !Object.prototype.hasOwnProperty.call(draft, "result")
        && Object.prototype.hasOwnProperty.call(committed, "result")
    ) {
        merged.result = committed.result;
    }

    if (
        !Object.prototype.hasOwnProperty.call(draft, "is_error")
        && Object.prototype.hasOwnProperty.call(committed, "is_error")
    ) {
        merged.is_error = committed.is_error;
    }

    if (
        !Object.prototype.hasOwnProperty.call(draft, "skill_content")
        && Object.prototype.hasOwnProperty.call(committed, "skill_content")
    ) {
        merged.skill_content = committed.skill_content;
    }

    return merged;
}

function areBlocksEquivalentForOverlap(committedBlock, draftBlock) {
    if (!committedBlock || !draftBlock || typeof committedBlock !== "object" || typeof draftBlock !== "object") {
        return false;
    }

    const committedType = committedBlock.type || "";
    const draftType = draftBlock.type || "";
    if (committedType !== draftType) {
        return false;
    }

    if (committedType === "tool_use") {
        return Boolean(committedBlock.id)
            && Boolean(draftBlock.id)
            && committedBlock.id === draftBlock.id;
    }

    if (committedType === "text") {
        return (committedBlock.text || "") === (draftBlock.text || "");
    }

    if (committedType === "thinking") {
        return (committedBlock.thinking || "") === (draftBlock.thinking || "");
    }

    if (committedType === "tool_result") {
        return (
            (committedBlock.tool_use_id || "") === (draftBlock.tool_use_id || "")
            && (committedBlock.content || "") === (draftBlock.content || "")
            && Boolean(committedBlock.is_error) === Boolean(draftBlock.is_error)
        );
    }

    if (committedType === "skill_content") {
        return (committedBlock.text || "") === (draftBlock.text || "");
    }

    return JSON.stringify(committedBlock) === JSON.stringify(draftBlock);
}

function findTailPrefixOverlap(committedContent, draftContent) {
    if (!Array.isArray(committedContent) || !Array.isArray(draftContent)) {
        return 0;
    }

    const maxOverlap = Math.min(committedContent.length, draftContent.length);
    for (let overlap = maxOverlap; overlap > 0; overlap -= 1) {
        let matched = true;
        for (let offset = 0; offset < overlap; offset += 1) {
            const committedIndex = committedContent.length - overlap + offset;
            if (!areBlocksEquivalentForOverlap(committedContent[committedIndex], draftContent[offset])) {
                matched = false;
                break;
            }
        }
        if (matched) {
            return overlap;
        }
    }

    return 0;
}

function overlapIncludesMatchingToolUse(committedContent, draftContent, overlap) {
    if (!Array.isArray(committedContent) || !Array.isArray(draftContent) || overlap <= 0) {
        return false;
    }

    for (let offset = 0; offset < overlap; offset += 1) {
        const committedIndex = committedContent.length - overlap + offset;
        const committedBlock = committedContent[committedIndex];
        const draftBlock = draftContent[offset];
        if (
            committedBlock
            && typeof committedBlock === "object"
            && committedBlock.type === "tool_use"
            && typeof committedBlock.id === "string"
            && committedBlock.id
            && draftBlock
            && typeof draftBlock === "object"
            && draftBlock.type === "tool_use"
            && committedBlock.id === draftBlock.id
        ) {
            return true;
        }
    }

    return false;
}

function collectCommittedToolUseIds(turns) {
    const ids = new Set();
    if (!Array.isArray(turns) || turns.length === 0) {
        return ids;
    }

    turns.forEach((turn) => {
        if (!turn || typeof turn !== "object" || !Array.isArray(turn.content)) {
            return;
        }
        turn.content.forEach((block) => {
            if (
                block
                && typeof block === "object"
                && block.type === "tool_use"
                && typeof block.id === "string"
                && block.id
            ) {
                ids.add(block.id);
            }
        });
    });

    return ids;
}

function mergeAssistantContentWithDraft(committedContent, draftContent, committedToolUseIds) {
    const merged = Array.isArray(committedContent) ? [...committedContent] : [];
    if (!Array.isArray(draftContent) || draftContent.length === 0) {
        return merged;
    }

    const toolUseIndexById = new Map();
    merged.forEach((block, index) => {
        if (
            block
            && typeof block === "object"
            && block.type === "tool_use"
            && typeof block.id === "string"
            && block.id
        ) {
            toolUseIndexById.set(block.id, index);
        }
    });

    const overlap = findTailPrefixOverlap(merged, draftContent);
    const effectiveOverlap = overlapIncludesMatchingToolUse(merged, draftContent, overlap) ? overlap : 0;
    if (effectiveOverlap > 0) {
        for (let i = 0; i < effectiveOverlap; i += 1) {
            const committedIndex = merged.length - effectiveOverlap + i;
            const committedBlock = merged[committedIndex];
            const draftBlock = draftContent[i];
            if (
                committedBlock
                && typeof committedBlock === "object"
                && committedBlock.type === "tool_use"
                && draftBlock
                && typeof draftBlock === "object"
                && draftBlock.type === "tool_use"
                && committedBlock.id
                && committedBlock.id === draftBlock.id
            ) {
                merged[committedIndex] = mergeToolUseBlocks(committedBlock, draftBlock);
            }
        }
    }

    draftContent.slice(effectiveOverlap).forEach((block) => {
        if (
            block
            && typeof block === "object"
            && block.type === "tool_use"
            && typeof block.id === "string"
            && block.id
        ) {
            const toolUseId = block.id;
            const existingIndex = toolUseIndexById.get(toolUseId);
            if (typeof existingIndex === "number") {
                merged[existingIndex] = mergeToolUseBlocks(merged[existingIndex], block);
                return;
            }

            if (committedToolUseIds.has(toolUseId)) {
                return;
            }

            toolUseIndexById.set(toolUseId, merged.length);
            committedToolUseIds.add(toolUseId);
        }

        merged.push(block);
    });

    return merged;
}

export function composeAssistantTurnsWithDraft(committedTurns, draftTurn) {
    const turns = Array.isArray(committedTurns) ? committedTurns : [];
    const draft = normalizeTurn(draftTurn);
    if (!draft) {
        return turns;
    }

    if (draft.type !== "assistant") {
        return [...turns, draft];
    }

    const committedToolUseIds = collectCommittedToolUseIds(turns);

    const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;
    if (
        lastTurn &&
        typeof lastTurn === "object" &&
        lastTurn.type === "assistant" &&
        Array.isArray(lastTurn.content)
    ) {
        const mergedContent = mergeAssistantContentWithDraft(
            lastTurn.content,
            draft.content,
            committedToolUseIds
        );
        return [
            ...turns.slice(0, -1),
            {
                ...lastTurn,
                content: mergedContent,
            },
        ];
    }

    const deduplicatedDraftContent = mergeAssistantContentWithDraft(
        [],
        draft.content,
        committedToolUseIds
    );
    if (deduplicatedDraftContent.length === 0) {
        return turns;
    }

    return [
        ...turns,
        {
            ...draft,
            content: deduplicatedDraftContent,
        },
    ];
}

function normalizeSessionStatusDetail(payload, fallbackStatus = "idle") {
    const rawStatus = typeof payload?.status === "string" ? payload.status.trim() : "";
    const fallback = typeof fallbackStatus === "string" ? fallbackStatus.trim() : "idle";
    const status = VALID_SESSION_STATUSES.has(rawStatus)
        ? rawStatus
        : VALID_SESSION_STATUSES.has(fallback)
            ? fallback
            : "idle";
    const subtype = typeof payload?.subtype === "string" && payload.subtype.trim()
        ? payload.subtype.trim()
        : null;
    const stopReason = typeof payload?.stop_reason === "string" && payload.stop_reason.trim()
        ? payload.stop_reason.trim()
        : null;
    const sessionId = typeof payload?.session_id === "string" && payload.session_id.trim()
        ? payload.session_id.trim()
        : "";
    const isError = typeof payload?.is_error === "boolean"
        ? payload.is_error
        : status === "error";

    return {
        status,
        subtype,
        stopReason,
        isError,
        sessionId,
    };
}

export function useAssistantState({
    initialProjectName,
    routeKind,
    currentProjectName,
    projects,
    pushToast,
}) {
    const [assistantPanelOpen, setAssistantPanelOpen] = useState(false);
    const [assistantScopeProject, setAssistantScopeProject] = useState(initialProjectName || "");
    const [assistantSessions, setAssistantSessions] = useState([]);
    const [assistantLoadingSessions, setAssistantLoadingSessions] = useState(false);
    const [assistantCurrentSessionId, setAssistantCurrentSessionId] = useState("");
    const [assistantMessages, setAssistantMessages] = useState([]);
    const [assistantDraftTurn, setAssistantDraftTurn] = useState(null);
    const [assistantMessagesLoading, setAssistantMessagesLoading] = useState(false);
    const [assistantInput, setAssistantInput] = useState("");
    const [assistantSending, setAssistantSending] = useState(false);
    const [assistantInterrupting, setAssistantInterrupting] = useState(false);
    const [assistantError, setAssistantError] = useState("");
    const [assistantPendingQuestion, setAssistantPendingQuestion] = useState(null);
    const [assistantAnsweringQuestion, setAssistantAnsweringQuestion] = useState(false);
    const [assistantSkills, setAssistantSkills] = useState([]);
    const [assistantSkillsLoading, setAssistantSkillsLoading] = useState(false);
    const [assistantRefreshToken, setAssistantRefreshToken] = useState(0);
    const [sessionStatus, setSessionStatus] = useState("idle");
    const [sessionStatusDetail, setSessionStatusDetail] = useState(() => (
        normalizeSessionStatusDetail({ status: "idle" })
    ));
    const [sessionDialogOpen, setSessionDialogOpen] = useState(false);
    const [sessionDialogMode, setSessionDialogMode] = useState("create");
    const [sessionDialogTitle, setSessionDialogTitle] = useState("");
    const [sessionDialogSessionId, setSessionDialogSessionId] = useState("");
    const [sessionDialogSubmitting, setSessionDialogSubmitting] = useState(false);
    const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
    const [deleteDialogSessionId, setDeleteDialogSessionId] = useState("");
    const [deleteDialogSessionTitle, setDeleteDialogSessionTitle] = useState("");
    const [deleteDialogSubmitting, setDeleteDialogSubmitting] = useState(false);

    const assistantStreamRef = useRef(null);
    const assistantChatScrollRef = useRef(null);
    const reconnectTimeoutRef = useRef(null);
    const sessionStatusRef = useRef("idle");
    const isUserScrolledUpRef = useRef(false);

    const assistantActive = assistantPanelOpen || routeKind === ROUTE_KIND.ASSISTANT;
    const currentAssistantProject = assistantScopeProject || currentProjectName || "";
    const assistantComposedMessages = useMemo(
        () => composeAssistantTurnsWithDraft(assistantMessages, assistantDraftTurn),
        [assistantMessages, assistantDraftTurn]
    );

    useEffect(() => {
        sessionStatusRef.current = sessionStatus;
    }, [sessionStatus]);

    // Project scope handling
    useEffect(() => {
        if (projects.length === 0) {
            setAssistantScopeProject("");
            return;
        }
        setAssistantScopeProject((prev) => prev || projects[0].name);
    }, [projects]);

    useEffect(() => {
        if (currentProjectName && assistantPanelOpen) {
            setAssistantScopeProject(currentProjectName);
        }
    }, [assistantPanelOpen, currentProjectName]);

    useEffect(() => {
        if (routeKind === ROUTE_KIND.ASSISTANT && assistantPanelOpen) {
            setAssistantPanelOpen(false);
        }
    }, [assistantPanelOpen, routeKind]);

    const closeActiveStream = useCallback(() => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
        }
        if (assistantStreamRef.current) {
            assistantStreamRef.current.close();
            assistantStreamRef.current = null;
        }
    }, []);

    useEffect(() => () => closeActiveStream(), [closeActiveStream]);

    const loadAssistantSessions = useCallback(async () => {
        if (!assistantActive) return;
        setAssistantLoadingSessions(true);
        try {
            const data = await window.API.listAssistantSessions(currentAssistantProject || null);
            const sessions = data.sessions || [];
            setAssistantSessions(sessions);
            setAssistantCurrentSessionId((prev) => {
                if (prev && sessions.some((s) => s.id === prev)) return prev;
                return sessions[0]?.id || "";
            });
        } catch (error) {
            pushToast(`加载会话失败：${error.message}`, "error");
        } finally {
            setAssistantLoadingSessions(false);
        }
    }, [assistantActive, currentAssistantProject, pushToast]);

    useEffect(() => {
        void loadAssistantSessions();
    }, [loadAssistantSessions, assistantRefreshToken]);

    const loadAssistantSkills = useCallback(async () => {
        if (!assistantActive) return;
        setAssistantSkillsLoading(true);
        try {
            const data = await window.API.listAssistantSkills(currentAssistantProject || null);
            setAssistantSkills(data.skills || []);
        } catch (error) {
            pushToast(`加载技能列表失败：${error.message}`, "error");
            setAssistantSkills([]);
        } finally {
            setAssistantSkillsLoading(false);
        }
    }, [assistantActive, currentAssistantProject, pushToast]);

    useEffect(() => {
        void loadAssistantSkills();
    }, [loadAssistantSkills]);

    const connectStream = useCallback((sessionId) => {
        closeActiveStream();

        const streamUrl = window.API.getAssistantStreamUrl(sessionId);
        const source = new EventSource(streamUrl);
        assistantStreamRef.current = source;

        source.addEventListener("snapshot", (event) => {
            const data = parseSsePayload(event);
            setAssistantMessages(Array.isArray(data.turns) ? data.turns : []);
            setAssistantDraftTurn(normalizeTurn(data.draft_turn));

            const questions = Array.isArray(data.pending_questions) ? data.pending_questions : [];
            const pending = questions.find(
                (item) => item && item.question_id && Array.isArray(item.questions) && item.questions.length > 0
            );
            if (pending) {
                setAssistantPendingQuestion({
                    id: pending.question_id,
                    questions: pending.questions,
                });
            } else {
                setAssistantPendingQuestion(null);
            }
            setAssistantAnsweringQuestion(false);

            if (typeof data.status === "string" && data.status) {
                const detail = normalizeSessionStatusDetail(data, data.status);
                setSessionStatus(detail.status);
                sessionStatusRef.current = detail.status;
                setSessionStatusDetail(detail);
                if (detail.status !== "running") {
                    setAssistantInterrupting(false);
                }
            }
        });

        source.addEventListener("patch", (event) => {
            const payload = parseSsePayload(event);
            const patch = payload.patch || payload;
            setAssistantMessages((previous) => applyTurnPatch(previous, patch));
            if (Object.prototype.hasOwnProperty.call(payload, "draft_turn")) {
                setAssistantDraftTurn(normalizeTurn(payload.draft_turn));
            }
        });

        source.addEventListener("delta", (event) => {
            const payload = parseSsePayload(event);
            if (Object.prototype.hasOwnProperty.call(payload, "draft_turn")) {
                setAssistantDraftTurn(normalizeTurn(payload.draft_turn));
            }
        });

        source.addEventListener("question", (event) => {
            const payload = parseSsePayload(event);
            const questions = Array.isArray(payload.questions) ? payload.questions : [];
            if (!payload.question_id || questions.length === 0) {
                return;
            }
            setAssistantPendingQuestion({
                id: payload.question_id,
                questions,
            });
            setAssistantAnsweringQuestion(false);
        });

        source.addEventListener("compact", () => {
            // Compact boundary means server-side history was rewritten; reconnect
            // to re-bootstrap from a fresh snapshot and avoid stale local state.
            if (assistantStreamRef.current !== source) {
                return;
            }
            connectStream(sessionId);
        });

        source.addEventListener("status", (event) => {
            const data = parseSsePayload(event);
            if (!data || typeof data !== "object") {
                return;
            }
            const detail = normalizeSessionStatusDetail(data, sessionStatusRef.current);
            sessionStatusRef.current = detail.status;
            setSessionStatus(detail.status);
            setSessionStatusDetail(detail);
            if (TERMINAL_SESSION_STATUSES.has(detail.status)) {
                setAssistantSending(false);
                setAssistantInterrupting(false);
                setAssistantPendingQuestion(null);
                setAssistantAnsweringQuestion(false);
                if (detail.status !== "interrupted") {
                    setAssistantDraftTurn(null);
                }
                closeActiveStream();
                setAssistantRefreshToken((prev) => prev + 1);
            }
        });

        source.onerror = () => {
            if (sessionStatusRef.current === "running") {
                reconnectTimeoutRef.current = setTimeout(() => {
                    connectStream(sessionId);
                }, 3000);
            }
        };
    }, [closeActiveStream]);

    const loadOrConnectSession = useCallback(async (sessionId) => {
        closeActiveStream();

        if (!sessionId) {
            setAssistantMessages([]);
            setAssistantDraftTurn(null);
            setSessionStatus("idle");
            sessionStatusRef.current = "idle";
            setSessionStatusDetail(normalizeSessionStatusDetail({ status: "idle" }));
            setAssistantSending(false);
            setAssistantInterrupting(false);
            setAssistantPendingQuestion(null);
            setAssistantAnsweringQuestion(false);
            return;
        }

        setAssistantMessagesLoading(true);
        setAssistantMessages([]);
        setAssistantDraftTurn(null);
        setAssistantInterrupting(false);
        setAssistantError("");

        try {
            const session = await window.API.getAssistantSession(sessionId);
            const sessionDetail = normalizeSessionStatusDetail({ status: session.status });
            setSessionStatus(sessionDetail.status);
            sessionStatusRef.current = sessionDetail.status;
            setSessionStatusDetail(sessionDetail);
            if (sessionDetail.status !== "running") {
                setAssistantSending(false);
            }

            if (sessionDetail.status === "running") {
                connectStream(sessionId);
            } else {
                const snapshot = await window.API.getAssistantSnapshot(sessionId);
                const snapshotDetail = normalizeSessionStatusDetail(snapshot, sessionStatusRef.current);
                setSessionStatus(snapshotDetail.status);
                sessionStatusRef.current = snapshotDetail.status;
                setSessionStatusDetail(snapshotDetail);
                setAssistantSending(false);
                setAssistantMessages(Array.isArray(snapshot.turns) ? snapshot.turns : []);
                setAssistantDraftTurn(normalizeTurn(snapshot.draft_turn));
                const questions = Array.isArray(snapshot.pending_questions)
                    ? snapshot.pending_questions
                    : [];
                const pending = questions.find(
                    (item) => item && item.question_id && Array.isArray(item.questions) && item.questions.length > 0
                );
                if (pending) {
                    setAssistantPendingQuestion({
                        id: pending.question_id,
                        questions: pending.questions,
                    });
                } else {
                    setAssistantPendingQuestion(null);
                }
                setAssistantAnsweringQuestion(false);
            }
        } catch (error) {
            pushToast(`加载消息失败：${error.message}`, "error");
        } finally {
            setAssistantMessagesLoading(false);
        }
    }, [closeActiveStream, connectStream, pushToast]);

    useEffect(() => {
        if (!assistantActive) return;
        void loadOrConnectSession(assistantCurrentSessionId);
    }, [assistantActive, assistantCurrentSessionId, loadOrConnectSession]);

    // Smart auto-scroll: pause when user scrolls up, resume when user scrolls back to bottom.
    // Uses wheel/touchmove to detect intentional user scrolling, avoiding false positives
    // from programmatic scrollTop assignments that also fire the scroll event.
    useEffect(() => {
        const el = assistantChatScrollRef.current;
        if (!el) return;
        isUserScrolledUpRef.current = false; // reset on session change
        const THRESHOLD = 50;

        const checkIfAtBottom = () => {
            const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
            if (distanceFromBottom <= THRESHOLD) {
                isUserScrolledUpRef.current = false;
            }
        };

        // Mark as scrolled-up only on intentional user interaction
        const handleUserScroll = () => {
            const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
            if (distanceFromBottom > THRESHOLD) {
                isUserScrolledUpRef.current = true;
            }
        };

        // After user interaction ends, also check if they scrolled to bottom
        const handleScrollEnd = () => { checkIfAtBottom(); };

        el.addEventListener("wheel", handleUserScroll, { passive: true });
        el.addEventListener("touchmove", handleUserScroll, { passive: true });
        el.addEventListener("scrollend", handleScrollEnd, { passive: true });

        return () => {
            el.removeEventListener("wheel", handleUserScroll);
            el.removeEventListener("touchmove", handleUserScroll);
            el.removeEventListener("scrollend", handleScrollEnd);
        };
    }, [assistantCurrentSessionId]);

    useEffect(() => {
        if (assistantChatScrollRef.current && !isUserScrolledUpRef.current) {
            assistantChatScrollRef.current.scrollTop = assistantChatScrollRef.current.scrollHeight;
        }
    }, [assistantComposedMessages, assistantCurrentSessionId, assistantMessagesLoading]);

    const ensureAssistantSession = useCallback(async () => {
        if (assistantCurrentSessionId) return assistantCurrentSessionId;

        const projectName = currentAssistantProject || projects[0]?.name;
        if (!projectName) throw new Error("请先创建至少一个项目");

        const data = await window.API.createAssistantSession(projectName, "");
        setAssistantSessions((prev) => [{ id: data.id, ...data }, ...prev]);
        setAssistantCurrentSessionId(data.id);
        return data.id;
    }, [assistantCurrentSessionId, currentAssistantProject, projects]);

    const handleSendAssistantMessage = useCallback(async (event) => {
        event.preventDefault();

        const content = assistantInput.trim();
        if (
            !content
            || assistantSending
            || assistantPendingQuestion
            || sessionStatusRef.current === "running"
        ) {
            return;
        }

        setAssistantSending(true);
        setAssistantError("");
        setAssistantInput("");

        try {
            const sessionId = await ensureAssistantSession();
            await window.API.sendAssistantMessage(sessionId, content);

            sessionStatusRef.current = "running";
            setSessionStatus("running");
            setSessionStatusDetail(normalizeSessionStatusDetail({ status: "running" }));
            connectStream(sessionId);
        } catch (error) {
            setAssistantError(error.message || "发送失败");
            setAssistantSending(false);
        }
    }, [assistantInput, assistantPendingQuestion, assistantSending, connectStream, ensureAssistantSession]);

    const handleInterruptAssistantSession = useCallback(async () => {
        if (!assistantCurrentSessionId) {
            return;
        }
        if (sessionStatusRef.current !== "running" || assistantInterrupting) {
            return;
        }

        setAssistantInterrupting(true);
        setAssistantError("");
        try {
            await window.API.interruptAssistantSession(assistantCurrentSessionId);
            if (!assistantStreamRef.current && sessionStatusRef.current === "running") {
                connectStream(assistantCurrentSessionId);
            }
        } catch (error) {
            setAssistantError(error.message || "中断失败");
            setAssistantInterrupting(false);
        }
    }, [assistantCurrentSessionId, assistantInterrupting, connectStream]);

    const handleAnswerAssistantQuestion = useCallback(async (questionId, answers) => {
        if (!assistantCurrentSessionId || !questionId) return;
        if (!answers || typeof answers !== "object" || Object.keys(answers).length === 0) {
            setAssistantError("请选择答案后再提交");
            return;
        }

        setAssistantAnsweringQuestion(true);
        setAssistantError("");
        try {
            await window.API.answerAssistantQuestion(assistantCurrentSessionId, questionId, answers);
            setAssistantPendingQuestion(null);
        } catch (error) {
            setAssistantError(error.message || "提交答案失败");
        } finally {
            setAssistantAnsweringQuestion(false);
        }
    }, [assistantCurrentSessionId]);

    // Session dialog handlers
    const handleCreateSession = useCallback(() => {
        const projectName = currentAssistantProject || projects[0]?.name;
        if (!projectName) {
            pushToast("请先创建项目", "error");
            return;
        }
        setSessionDialogMode("create");
        setSessionDialogSessionId("");
        setSessionDialogTitle("");
        setSessionDialogOpen(true);
    }, [currentAssistantProject, projects, pushToast]);

    const handleRenameSession = useCallback((session) => {
        if (!session?.id) return;
        setSessionDialogMode("rename");
        setSessionDialogSessionId(session.id);
        setSessionDialogTitle(session.title || "");
        setSessionDialogOpen(true);
    }, []);

    const closeSessionDialog = useCallback(() => {
        if (sessionDialogSubmitting) return;
        setSessionDialogOpen(false);
        setSessionDialogMode("create");
        setSessionDialogTitle("");
        setSessionDialogSessionId("");
    }, [sessionDialogSubmitting]);

    const submitSessionDialog = useCallback(async (event) => {
        event.preventDefault();
        if (sessionDialogSubmitting) return;

        setSessionDialogSubmitting(true);
        try {
            if (sessionDialogMode === "create") {
                const projectName = currentAssistantProject || projects[0]?.name;
                if (!projectName) {
                    pushToast("请先创建项目", "error");
                    return;
                }
                const data = await window.API.createAssistantSession(projectName, sessionDialogTitle.trim());
                setAssistantCurrentSessionId(data.id);
                setAssistantRefreshToken((prev) => prev + 1);
                pushToast("已创建新会话", "success");
            } else {
                const normalized = sessionDialogTitle.trim();
                if (!normalized) {
                    pushToast("标题不能为空", "error");
                    return;
                }
                if (!sessionDialogSessionId) {
                    pushToast("未找到会话", "error");
                    return;
                }
                await window.API.updateAssistantSession(sessionDialogSessionId, { title: normalized });
                setAssistantRefreshToken((prev) => prev + 1);
                pushToast("会话已重命名", "success");
            }
            setSessionDialogOpen(false);
            setSessionDialogMode("create");
            setSessionDialogTitle("");
            setSessionDialogSessionId("");
        } catch (error) {
            pushToast(`保存会话失败：${error.message}`, "error");
        } finally {
            setSessionDialogSubmitting(false);
        }
    }, [currentAssistantProject, projects, pushToast, sessionDialogMode, sessionDialogSessionId, sessionDialogSubmitting, sessionDialogTitle]);

    // Delete dialog handlers
    const handleDeleteSession = useCallback((session) => {
        if (!session?.id) return;
        setDeleteDialogSessionId(session.id);
        setDeleteDialogSessionTitle(session.title || "");
        setDeleteDialogOpen(true);
    }, []);

    const closeDeleteDialog = useCallback(() => {
        if (deleteDialogSubmitting) return;
        setDeleteDialogOpen(false);
        setDeleteDialogSessionId("");
        setDeleteDialogSessionTitle("");
    }, [deleteDialogSubmitting]);

    const confirmDeleteSession = useCallback(async (event) => {
        event.preventDefault();
        if (deleteDialogSubmitting) return;
        if (!deleteDialogSessionId) {
            pushToast("未找到会话", "error");
            return;
        }

        setDeleteDialogSubmitting(true);
        try {
            await window.API.deleteAssistantSession(deleteDialogSessionId);
            if (assistantCurrentSessionId === deleteDialogSessionId) {
                setAssistantCurrentSessionId("");
                setAssistantMessages([]);
                setAssistantDraftTurn(null);
                setSessionStatus("idle");
                sessionStatusRef.current = "idle";
                setSessionStatusDetail(normalizeSessionStatusDetail({ status: "idle" }));
            }
            setAssistantRefreshToken((prev) => prev + 1);
            pushToast("会话已删除", "success");
            setDeleteDialogOpen(false);
            setDeleteDialogSessionId("");
            setDeleteDialogSessionTitle("");
        } catch (error) {
            pushToast(`删除失败：${error.message}`, "error");
        } finally {
            setDeleteDialogSubmitting(false);
        }
    }, [assistantCurrentSessionId, deleteDialogSessionId, deleteDialogSubmitting, pushToast]);

    const handleAssistantScopeChange = useCallback((projectName) => {
        setAssistantScopeProject(projectName);
        setAssistantCurrentSessionId("");
        setAssistantRefreshToken((prev) => prev + 1);
    }, []);

    const toggleAssistantPanel = useCallback(() => {
        if (!assistantPanelOpen && currentProjectName) {
            setAssistantScopeProject(currentProjectName);
        }
        setAssistantPanelOpen((prev) => !prev);
    }, [assistantPanelOpen, currentProjectName]);

    return {
        assistantPanelOpen,
        setAssistantPanelOpen,
        assistantSessions,
        assistantLoadingSessions,
        assistantCurrentSessionId,
        setAssistantCurrentSessionId,
        assistantMessagesLoading,
        assistantInput,
        setAssistantInput,
        assistantSending,
        assistantInterrupting,
        assistantError,
        assistantSkills,
        assistantSkillsLoading,
        assistantComposedMessages,
        assistantPendingQuestion,
        assistantAnsweringQuestion,
        currentAssistantProject,
        sessionStatus,
        sessionStatusDetail,
        sessionDialogOpen,
        sessionDialogMode,
        sessionDialogTitle,
        setSessionDialogTitle,
        sessionDialogSubmitting,
        deleteDialogOpen,
        deleteDialogSessionTitle,
        deleteDialogSubmitting,
        handleSendAssistantMessage,
        handleInterruptAssistantSession,
        handleCreateSession,
        handleRenameSession,
        handleDeleteSession,
        closeSessionDialog,
        submitSessionDialog,
        closeDeleteDialog,
        confirmDeleteSession,
        handleAssistantScopeChange,
        handleAnswerAssistantQuestion,
        toggleAssistantPanel,
        assistantChatScrollRef,
    };
}
