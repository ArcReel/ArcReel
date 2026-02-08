import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 任务状态管理 Hook
 * 通过 SSE 实时接收任务更新，维护任务列表和统计数据
 */
export function useTasksState({ projectName = null, pushToast = null } = {}) {
    const [tasks, setTasks] = useState([]);
    const [stats, setStats] = useState({ queued: 0, running: 0, succeeded: 0, failed: 0 });
    const [lastEventId, setLastEventId] = useState(0);
    const [connected, setConnected] = useState(false);

    const eventSourceRef = useRef(null);
    const reconnectTimeoutRef = useRef(null);

    // 更新或插入任务
    const upsertTask = useCallback((taskData) => {
        setTasks((prev) => {
            const idx = prev.findIndex((t) => t.task_id === taskData.task_id);
            if (idx >= 0) {
                const updated = [...prev];
                updated[idx] = { ...updated[idx], ...taskData };
                return updated;
            }
            return [taskData, ...prev];
        });
    }, []);

    // 从快照初始化
    const handleSnapshot = useCallback((payload) => {
        if (payload.tasks) {
            setTasks(payload.tasks);
        }
        if (payload.stats) {
            setStats(payload.stats);
        }
        if (payload.last_event_id) {
            setLastEventId(payload.last_event_id);
        }
    }, []);

    // 处理增量任务事件
    const handleTaskEvent = useCallback(
        (payload, event) => {
            const eventId = event?.lastEventId ? Number(event.lastEventId) : null;
            if (eventId && eventId > lastEventId) {
                setLastEventId(eventId);
            }

            const task = payload.task;
            if (!task) return;

            upsertTask(task);

            // 更新统计
            if (payload.stats) {
                setStats(payload.stats);
            }

            // 任务完成时推送 toast
            if (pushToast && (task.status === "succeeded" || task.status === "failed")) {
                const resourceLabel = task.resource_id || task.task_type;
                if (task.status === "succeeded") {
                    pushToast(`✅ ${resourceLabel} 生成完成`, "success");
                } else {
                    pushToast(`❌ ${resourceLabel} 生成失败`, "error");
                }
            }
        },
        [lastEventId, pushToast, upsertTask]
    );

    // 建立 SSE 连接
    const connect = useCallback(() => {
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
        }

        const source = window.API.openTaskStream({
            projectName,
            lastEventId: lastEventId > 0 ? lastEventId : undefined,
            onSnapshot: (payload) => {
                setConnected(true);
                handleSnapshot(payload);
            },
            onTask: handleTaskEvent,
            onHeartbeat: () => {
                setConnected(true);
            },
            onError: () => {
                setConnected(false);
                // 自动重连
                if (reconnectTimeoutRef.current) {
                    clearTimeout(reconnectTimeoutRef.current);
                }
                reconnectTimeoutRef.current = setTimeout(() => {
                    connect();
                }, 3000);
            },
        });

        eventSourceRef.current = source;
    }, [handleSnapshot, handleTaskEvent, lastEventId, projectName]);

    // 断开 SSE 连接
    const disconnect = useCallback(() => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
        }
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
            eventSourceRef.current = null;
        }
        setConnected(false);
    }, []);

    // 生命周期管理
    useEffect(() => {
        connect();
        return () => {
            disconnect();
        };
    }, [connect, disconnect]);

    // 刷新任务列表
    const refreshTasks = useCallback(async () => {
        try {
            const data = await window.API.listTasks({ projectName });
            if (data.tasks) {
                setTasks(data.tasks);
            }
        } catch (error) {
            if (pushToast) {
                pushToast(`刷新任务失败：${error.message}`, "error");
            }
        }
    }, [projectName, pushToast]);

    // 刷新统计
    const refreshStats = useCallback(async () => {
        try {
            const data = await window.API.getTaskStats(projectName);
            if (data.stats) {
                setStats(data.stats);
            }
        } catch (error) {
            // 静默失败
        }
    }, [projectName]);

    // 按状态过滤的任务
    const queuedTasks = tasks.filter((t) => t.status === "queued");
    const runningTasks = tasks.filter((t) => t.status === "running");
    const completedTasks = tasks.filter((t) => t.status === "succeeded" || t.status === "failed");

    return {
        tasks,
        stats,
        connected,
        queuedTasks,
        runningTasks,
        completedTasks,
        refreshTasks,
        refreshStats,
        connect,
        disconnect,
    };
}
