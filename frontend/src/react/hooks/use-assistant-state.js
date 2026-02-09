import { useCallback, useEffect, useRef, useState } from "react";

import { ROUTE_KIND } from "../constants.js";

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
    const [assistantMessagesLoading, setAssistantMessagesLoading] = useState(false);
    const [assistantInput, setAssistantInput] = useState("");
    const [assistantSending, setAssistantSending] = useState(false);
    const [assistantError, setAssistantError] = useState("");
    const [assistantPendingQuestion, setAssistantPendingQuestion] = useState(null);
    const [assistantAnsweringQuestion, setAssistantAnsweringQuestion] = useState(false);
    const [assistantSkills, setAssistantSkills] = useState([]);
    const [assistantSkillsLoading, setAssistantSkillsLoading] = useState(false);
    const [assistantRefreshToken, setAssistantRefreshToken] = useState(0);
    const [sessionStatus, setSessionStatus] = useState("idle");
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

    const assistantActive = assistantPanelOpen || routeKind === ROUTE_KIND.ASSISTANT;
    const currentAssistantProject = assistantScopeProject || currentProjectName || "";
    const assistantComposedMessages = assistantMessages;

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

        source.addEventListener("turn_snapshot", (event) => {
            const data = parseSsePayload(event);
            setAssistantMessages(Array.isArray(data.turns) ? data.turns : []);
        });

        source.addEventListener("turn_patch", (event) => {
            const patch = parseSsePayload(event);
            setAssistantMessages((previous) => applyTurnPatch(previous, patch));
        });

        // Backward compatibility: raw SSE messages are ignored for rendering.
        source.addEventListener("message", (event) => {
            const message = parseSsePayload(event);
            if (message.type === "ask_user_question") {
                const questions = Array.isArray(message.questions) ? message.questions : [];
                if (message.question_id && questions.length > 0) {
                    setAssistantPendingQuestion({
                        id: message.question_id,
                        questions,
                    });
                    setAssistantAnsweringQuestion(false);
                }
                return;
            }
            if (message.type === "result") {
                const isSuccess = message.subtype === "success";
                const status = isSuccess ? "completed" : "error";
                sessionStatusRef.current = status;
                setSessionStatus(status);
                setAssistantSending(false);
                setAssistantPendingQuestion(null);
                setAssistantAnsweringQuestion(false);
                closeActiveStream();
            }
        });

        source.addEventListener("status", (event) => {
            const data = parseSsePayload(event);
            const status = data.status;
            if (!status) return;
            sessionStatusRef.current = status;
            setSessionStatus(status);
            if (status === "completed" || status === "error") {
                setAssistantSending(false);
                setAssistantPendingQuestion(null);
                setAssistantAnsweringQuestion(false);
                closeActiveStream();
            }
        });

        source.addEventListener("ping", () => {
            // Heartbeat only.
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
            setSessionStatus("idle");
            sessionStatusRef.current = "idle";
            setAssistantPendingQuestion(null);
            setAssistantAnsweringQuestion(false);
            return;
        }

        setAssistantMessagesLoading(true);
        setAssistantMessages([]);
        setAssistantError("");

        try {
            const session = await window.API.getAssistantSession(sessionId);
            setSessionStatus(session.status);
            sessionStatusRef.current = session.status;

            if (session.status === "running") {
                connectStream(sessionId);
            } else {
                const data = await window.API.listAssistantMessages(sessionId);
                setAssistantMessages(Array.isArray(data.messages) ? data.messages : []);
                setAssistantPendingQuestion(null);
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

    useEffect(() => {
        if (assistantChatScrollRef.current) {
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
        if (!content || assistantSending || assistantPendingQuestion) return;

        setAssistantSending(true);
        setAssistantError("");
        setAssistantInput("");

        try {
            const sessionId = await ensureAssistantSession();
            await window.API.sendAssistantMessage(sessionId, content);

            sessionStatusRef.current = "running";
            setSessionStatus("running");
            connectStream(sessionId);
        } catch (error) {
            setAssistantError(error.message || "发送失败");
            setAssistantSending(false);
        }
    }, [assistantInput, assistantPendingQuestion, assistantSending, connectStream, ensureAssistantSession]);

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
                setSessionStatus("idle");
                sessionStatusRef.current = "idle";
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
        assistantError,
        assistantSkills,
        assistantSkillsLoading,
        assistantComposedMessages,
        assistantPendingQuestion,
        assistantAnsweringQuestion,
        currentAssistantProject,
        sessionStatus,
        sessionDialogOpen,
        sessionDialogMode,
        sessionDialogTitle,
        setSessionDialogTitle,
        sessionDialogSubmitting,
        deleteDialogOpen,
        deleteDialogSessionTitle,
        deleteDialogSubmitting,
        handleSendAssistantMessage,
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
