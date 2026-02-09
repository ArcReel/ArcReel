import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ROUTE_KIND } from "../constants.js";
import { parseStreamData } from "../utils.js";

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
    const [assistantStreamingBlocks, setAssistantStreamingBlocks] = useState([]);
    const [assistantError, setAssistantError] = useState("");
    const [assistantSkills, setAssistantSkills] = useState([]);
    const [assistantSkillsLoading, setAssistantSkillsLoading] = useState(false);
    const [assistantRefreshToken, setAssistantRefreshToken] = useState(0);
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

    const assistantActive = assistantPanelOpen || routeKind === ROUTE_KIND.ASSISTANT;
    const currentAssistantProject = assistantScopeProject || currentProjectName || "";

    const assistantComposedMessages = useMemo(() => {
        const base = Array.isArray(assistantMessages) ? [...assistantMessages] : [];
        if (assistantStreamingBlocks.length > 0) {
            base.push({
                id: "streaming-assistant",
                role: "assistant",
                content: assistantStreamingBlocks,
            });
        }
        return base;
    }, [assistantMessages, assistantStreamingBlocks]);

    useEffect(() => {
        if (projects.length === 0) {
            setAssistantScopeProject("");
            return;
        }

        setAssistantScopeProject((previous) => previous || projects[0].name);
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
        if (!assistantStreamRef.current) {
            return;
        }

        assistantStreamRef.current.close();
        assistantStreamRef.current = null;
    }, []);

    useEffect(() => () => closeActiveStream(), [closeActiveStream]);

    const loadAssistantSessions = useCallback(async () => {
        if (!assistantActive) {
            return;
        }

        setAssistantLoadingSessions(true);
        try {
            const data = await window.API.listAssistantSessions(currentAssistantProject || null);
            const sessions = data.sessions || [];
            setAssistantSessions(sessions);

            setAssistantCurrentSessionId((previous) => {
                if (previous && sessions.some((session) => session.id === previous)) {
                    return previous;
                }
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
        if (!assistantActive) {
            return;
        }

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

    const loadAssistantMessages = useCallback(
        async (sessionId) => {
            if (!sessionId) {
                setAssistantMessages([]);
                return;
            }

            setAssistantMessagesLoading(true);
            try {
                const data = await window.API.listAssistantMessages(sessionId);
                setAssistantMessages(data.messages || []);
            } catch (error) {
                pushToast(`加载消息失败：${error.message}`, "error");
            } finally {
                setAssistantMessagesLoading(false);
            }
        },
        [pushToast]
    );

    useEffect(() => {
        if (!assistantActive) {
            return;
        }

        setAssistantStreamingBlocks([]);
        setAssistantError("");
        void loadAssistantMessages(assistantCurrentSessionId);
    }, [assistantActive, assistantCurrentSessionId, loadAssistantMessages]);

    useEffect(() => {
        if (!assistantChatScrollRef.current) {
            return;
        }

        assistantChatScrollRef.current.scrollTop = assistantChatScrollRef.current.scrollHeight;
    }, [assistantComposedMessages, assistantCurrentSessionId, assistantMessagesLoading]);

    const ensureAssistantSession = useCallback(async () => {
        if (assistantCurrentSessionId) {
            return assistantCurrentSessionId;
        }

        const projectName = currentAssistantProject || projects[0]?.name;
        if (!projectName) {
            throw new Error("请先创建至少一个项目");
        }

        const data = await window.API.createAssistantSession(projectName, "");
        const created = data.session;

        setAssistantSessions((previous) => [created, ...previous]);
        setAssistantCurrentSessionId(created.id);
        return created.id;
    }, [assistantCurrentSessionId, currentAssistantProject, projects]);

    const consumeAssistantStream = useCallback(
        (streamUrl) => {
            return new Promise((resolve, reject) => {
                closeActiveStream();

                const source = new EventSource(streamUrl);
                assistantStreamRef.current = source;
                let settled = false;

                const finish = (error = null) => {
                    if (settled) {
                        return;
                    }

                    settled = true;
                    source.close();
                    if (assistantStreamRef.current === source) {
                        assistantStreamRef.current = null;
                    }

                    if (error) {
                        reject(error);
                    } else {
                        resolve();
                    }
                };

                source.addEventListener("ack", () => {
                    // Connection established, no action needed
                });

                source.addEventListener("delta", (event) => {
                    const data = parseStreamData(event);
                    const chunk = typeof data.text === "string" ? data.text : "";
                    if (!chunk) {
                        return;
                    }

                    setAssistantStreamingBlocks((previous) => {
                        // Find the last text block and append to it
                        const lastBlock = previous[previous.length - 1];
                        if (lastBlock && lastBlock.type === "text") {
                            return [
                                ...previous.slice(0, -1),
                                { ...lastBlock, text: lastBlock.text + chunk },
                            ];
                        }
                        // Create a new text block
                        return [...previous, { type: "text", text: chunk }];
                    });
                });

                source.addEventListener("tool_use", (event) => {
                    const data = parseStreamData(event);
                    setAssistantStreamingBlocks((previous) => [
                        ...previous,
                        {
                            type: "tool_use",
                            id: data.id || "",
                            name: data.name || "",
                            input: data.input || {},
                        },
                    ]);
                });

                source.addEventListener("tool_result", (event) => {
                    const data = parseStreamData(event);
                    setAssistantStreamingBlocks((previous) => [
                        ...previous,
                        {
                            type: "tool_result",
                            tool_use_id: data.tool_use_id || "",
                            content: data.content || "",
                            is_error: data.is_error || false,
                        },
                    ]);
                });

                source.addEventListener("done", () => {
                    finish();
                });

                source.addEventListener("error", (event) => {
                    const data = parseStreamData(event);
                    const message = data.message || "流式连接中断";
                    finish(new Error(message));
                });
            });
        },
        [closeActiveStream]
    );

    const handleSendAssistantMessage = useCallback(
        async (event) => {
            event.preventDefault();

            const content = assistantInput.trim();
            if (!content || assistantSending) {
                return;
            }

            setAssistantSending(true);
            setAssistantError("");
            setAssistantInput("");
            setAssistantStreamingBlocks([]);

            try {
                const sessionId = await ensureAssistantSession();
                setAssistantMessages((previous) => [
                    ...previous,
                    { id: `tmp-${Date.now()}`, role: "user", content },
                ]);

                const start = await window.API.startAssistantMessageStream(sessionId, content);
                await consumeAssistantStream(start.stream_url);
                setAssistantRefreshToken((previous) => previous + 1);
                await loadAssistantMessages(sessionId);
                setAssistantStreamingBlocks([]);
            } catch (error) {
                setAssistantError(error.message || "发送失败");
                setAssistantStreamingBlocks((previous) => [
                    ...previous,
                    { type: "text", text: `\n\n错误：${error.message || "发送失败"}` },
                ]);
            } finally {
                setAssistantSending(false);
            }
        },
        [
            assistantInput,
            assistantSending,
            consumeAssistantStream,
            ensureAssistantSession,
            loadAssistantMessages,
        ]
    );

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

    const handleRenameSession = useCallback(
        (session) => {
            if (!session?.id) {
                return;
            }

            setSessionDialogMode("rename");
            setSessionDialogSessionId(session.id);
            setSessionDialogTitle(session.title || "");
            setSessionDialogOpen(true);
        },
        []
    );

    const closeSessionDialog = useCallback(() => {
        if (sessionDialogSubmitting) {
            return;
        }

        setSessionDialogOpen(false);
        setSessionDialogMode("create");
        setSessionDialogTitle("");
        setSessionDialogSessionId("");
    }, [sessionDialogSubmitting]);

    const submitSessionDialog = useCallback(
        async (event) => {
            event.preventDefault();
            if (sessionDialogSubmitting) {
                return;
            }

            setSessionDialogSubmitting(true);
            try {
                if (sessionDialogMode === "create") {
                    const projectName = currentAssistantProject || projects[0]?.name;
                    if (!projectName) {
                        pushToast("请先创建项目", "error");
                        return;
                    }

                    const data = await window.API.createAssistantSession(projectName, sessionDialogTitle.trim());
                    setAssistantCurrentSessionId(data.session.id);
                    setAssistantRefreshToken((previous) => previous + 1);
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
                    setAssistantRefreshToken((previous) => previous + 1);
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
        },
        [
            currentAssistantProject,
            projects,
            pushToast,
            sessionDialogMode,
            sessionDialogSessionId,
            sessionDialogSubmitting,
            sessionDialogTitle,
        ]
    );

    const handleDeleteSession = useCallback((session) => {
        if (!session?.id) {
            return;
        }

        setDeleteDialogSessionId(session.id);
        setDeleteDialogSessionTitle(session.title || "");
        setDeleteDialogOpen(true);
    }, []);

    const closeDeleteDialog = useCallback(() => {
        if (deleteDialogSubmitting) {
            return;
        }

        setDeleteDialogOpen(false);
        setDeleteDialogSessionId("");
        setDeleteDialogSessionTitle("");
    }, [deleteDialogSubmitting]);

    const confirmDeleteSession = useCallback(
        async (event) => {
            event.preventDefault();
            if (deleteDialogSubmitting) {
                return;
            }

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
                setAssistantRefreshToken((previous) => previous + 1);
                pushToast("会话已删除", "success");
                setDeleteDialogOpen(false);
                setDeleteDialogSessionId("");
                setDeleteDialogSessionTitle("");
            } catch (error) {
                pushToast(`删除失败：${error.message}`, "error");
            } finally {
                setDeleteDialogSubmitting(false);
            }
        },
        [assistantCurrentSessionId, deleteDialogSessionId, deleteDialogSubmitting, pushToast]
    );

    const handleAssistantScopeChange = useCallback((projectName) => {
        setAssistantScopeProject(projectName);
        setAssistantCurrentSessionId("");
        setAssistantRefreshToken((previous) => previous + 1);
    }, []);

    const toggleAssistantPanel = useCallback(() => {
        if (!assistantPanelOpen && currentProjectName) {
            setAssistantScopeProject(currentProjectName);
        }

        setAssistantPanelOpen((previous) => !previous);
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
