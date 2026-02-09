import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ROUTE_KIND } from "../constants.js";

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
    const [assistantStreamingMessage, setAssistantStreamingMessage] = useState(null);
    const [assistantError, setAssistantError] = useState("");
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

    const assistantActive = assistantPanelOpen || routeKind === ROUTE_KIND.ASSISTANT;
    const currentAssistantProject = assistantScopeProject || currentProjectName || "";

    // Composed messages: historical + streaming
    const assistantComposedMessages = useMemo(() => {
        const base = Array.isArray(assistantMessages) ? [...assistantMessages] : [];
        if (assistantStreamingMessage) {
            base.push(assistantStreamingMessage);
        }
        return base;
    }, [assistantMessages, assistantStreamingMessage]);

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

    // Close stream helper
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

    // Load sessions
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

    // Load skills
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

    // Connect to SSE stream
    const connectStream = useCallback((sessionId) => {
        closeActiveStream();

        const streamUrl = window.API.getAssistantStreamUrl(sessionId);
        const source = new EventSource(streamUrl);
        assistantStreamRef.current = source;

        source.addEventListener("message", (event) => {
            try {
                const message = JSON.parse(event.data);
                setAssistantMessages((prev) => [...prev, message]);

                // Check for result message
                if (message.type === "result") {
                    setSessionStatus(message.subtype === "success" ? "completed" : "error");
                    setAssistantStreamingMessage(null);
                    setAssistantSending(false);
                    closeActiveStream();
                }
            } catch (err) {
                console.error("Failed to parse SSE message:", err);
            }
        });

        source.addEventListener("status", (event) => {
            try {
                const data = JSON.parse(event.data);
                setSessionStatus(data.status);
                if (data.status === "completed" || data.status === "error") {
                    closeActiveStream();
                }
            } catch (err) {
                console.error("Failed to parse status event:", err);
            }
        });

        source.addEventListener("ping", () => {
            // Heartbeat, no action needed
        });

        source.onerror = () => {
            // Reconnect after 3 seconds if session is running
            if (sessionStatus === "running") {
                reconnectTimeoutRef.current = setTimeout(() => {
                    connectStream(sessionId);
                }, 3000);
            }
        };
    }, [closeActiveStream, sessionStatus]);

    // Load messages or connect stream based on status
    const loadOrConnectSession = useCallback(async (sessionId) => {
        if (!sessionId) {
            setAssistantMessages([]);
            setSessionStatus("idle");
            return;
        }

        setAssistantMessagesLoading(true);
        setAssistantStreamingMessage(null);
        setAssistantError("");

        try {
            // Get session status
            const session = await window.API.getAssistantSession(sessionId);
            setSessionStatus(session.status);

            if (session.status === "running") {
                // Connect to stream for live updates
                connectStream(sessionId);
            } else {
                // Load history from transcript
                const data = await window.API.listAssistantMessages(sessionId);
                setAssistantMessages(data.messages || []);
            }
        } catch (error) {
            pushToast(`加载消息失败：${error.message}`, "error");
        } finally {
            setAssistantMessagesLoading(false);
        }
    }, [connectStream, pushToast]);

    useEffect(() => {
        if (!assistantActive) return;
        void loadOrConnectSession(assistantCurrentSessionId);
    }, [assistantActive, assistantCurrentSessionId, loadOrConnectSession]);

    // Auto scroll
    useEffect(() => {
        if (assistantChatScrollRef.current) {
            assistantChatScrollRef.current.scrollTop = assistantChatScrollRef.current.scrollHeight;
        }
    }, [assistantComposedMessages, assistantCurrentSessionId, assistantMessagesLoading]);

    // Ensure session exists
    const ensureAssistantSession = useCallback(async () => {
        if (assistantCurrentSessionId) return assistantCurrentSessionId;

        const projectName = currentAssistantProject || projects[0]?.name;
        if (!projectName) throw new Error("请先创建至少一个项目");

        const data = await window.API.createAssistantSession(projectName, "");
        setAssistantSessions((prev) => [{ id: data.id, ...data }, ...prev]);
        setAssistantCurrentSessionId(data.id);
        return data.id;
    }, [assistantCurrentSessionId, currentAssistantProject, projects]);

    // Send message
    const handleSendAssistantMessage = useCallback(async (event) => {
        event.preventDefault();

        const content = assistantInput.trim();
        if (!content || assistantSending) return;

        setAssistantSending(true);
        setAssistantError("");
        setAssistantInput("");
        setAssistantStreamingMessage(null);

        try {
            const sessionId = await ensureAssistantSession();

            // Add optimistic user message
            setAssistantMessages((prev) => [
                ...prev,
                { type: "user", content, id: `tmp-${Date.now()}` },
            ]);

            // Send and connect to stream
            await window.API.sendAssistantMessage(sessionId, content);
            setSessionStatus("running");
            connectStream(sessionId);
        } catch (error) {
            setAssistantError(error.message || "发送失败");
            setAssistantSending(false);
        }
    }, [assistantInput, assistantSending, connectStream, ensureAssistantSession]);

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
        toggleAssistantPanel,
        assistantChatScrollRef,
    };
}
