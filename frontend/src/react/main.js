import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import htm from "htm";

import { ROUTE_KIND } from "./constants.js";
import { AssistantDeleteSessionDialog } from "./components/assistant-delete-session-dialog.js";
import { AssistantFloatingMount } from "./components/assistant-floating-mount.js";
import { AssistantSessionDialog } from "./components/assistant-session-dialog.js";
import { AssistantMessageAreaMount } from "./components/assistant-message-area-mount.js";
import { DashboardRouterView } from "./components/dashboard-router-view.js";
import { Button } from "./components/primitives.js";
import { AppToast } from "./components/app-toast.js";
import { AppShell } from "./components/app-shell.js";
import { CreateProjectModal } from "./components/create-project-modal.js";
import { LandingPage } from "./components/landing-page.js";
import { useAppRouter } from "./hooks/use-app-router.js";
import { useDocumentTitle } from "./hooks/use-document-title.js";
import { useProjectsState } from "./hooks/use-projects-state.js";
import { useTasksState } from "./hooks/use-tasks-state.js";
import { useUsageState } from "./hooks/use-usage-state.js";
import { useAssistantState } from "./hooks/use-assistant-state.js";

const html = htm.bind(React.createElement);

function App() {
    const {
        initialProjectName,
        route,
        projectTab,
        setProjectTab,
        selectedProject,
        setSelectedProject,
        navigate,
    } = useAppRouter();
    const [toast, setToast] = useState(null);

    const pushToast = useCallback((text, tone = "info") => {
        setToast({ id: `${Date.now()}-${Math.random()}`, text, tone });
    }, []);

    useEffect(() => {
        if (!toast) {
            return undefined;
        }

        const timer = window.setTimeout(() => setToast(null), 2800);
        return () => window.clearTimeout(timer);
    }, [toast]);

    const {
        projects,
        projectsLoading,
        projectDetailLoading,
        showCreateModal,
        setShowCreateModal,
        creatingProject,
        createForm,
        setCreateForm,
        currentProjectName,
        selectedProjectItem,
        currentProjectData,
        currentScripts,
        loadProjects,
        handleCreateProject,
        handleDeleteProject,
        handleRefreshCurrentProject,
    } = useProjectsState({
        selectedProject,
        setSelectedProject,
        route,
        projectTab,
        navigate,
        pushToast,
    });

    const {
        usageProjects,
        usageFilters,
        setUsageFilters,
        usageStats,
        usageCalls,
        usageTotal,
        usagePage,
        setUsagePage,
        usageLoading,
        usagePageSize,
    } = useUsageState({
        routeKind: route.kind,
        pushToast,
    });

    const {
        tasks: tasksData,
        stats: tasksStats,
        connected: tasksConnected,
        queuedTasks: tasksQueuedTasks,
        runningTasks: tasksRunningTasks,
        completedTasks: tasksCompletedTasks,
        refreshTasks: tasksRefresh,
    } = useTasksState({
        pushToast,
    });

    const {
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
    } = useAssistantState({
        initialProjectName,
        routeKind: route.kind,
        currentProjectName,
        projects,
        pushToast,
    });

    useDocumentTitle(route.kind, currentProjectName);

    const isLanding = route.kind === ROUTE_KIND.LANDING;
    const dashboardKind = route.kind === ROUTE_KIND.WORKSPACE ? ROUTE_KIND.WORKSPACE : route.kind;

    const assistantMessageArea = html`
        <${AssistantMessageAreaMount}
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
            handleSendAssistantMessage=${handleSendAssistantMessage}
            handleInterruptAssistantSession=${handleInterruptAssistantSession}
            handleAnswerAssistantQuestion=${handleAnswerAssistantQuestion}
            assistantChatScrollRef=${assistantChatScrollRef}
        />
    `;

    const dashboardContent = html`
        <${DashboardRouterView}
            route=${route}
            selectedProject=${selectedProject}
            setSelectedProject=${setSelectedProject}
            projects=${projects}
            projectsLoading=${projectsLoading}
            navigate=${navigate}
            loadProjects=${loadProjects}
            setShowCreateModal=${setShowCreateModal}
            currentProjectData=${currentProjectData}
            currentProjectName=${currentProjectName}
            projectTab=${projectTab}
            setProjectTab=${setProjectTab}
            handleRefreshCurrentProject=${handleRefreshCurrentProject}
            handleDeleteProject=${handleDeleteProject}
            projectDetailLoading=${projectDetailLoading}
            currentScripts=${currentScripts}
            assistantLoadingSessions=${assistantLoadingSessions}
            assistantSessions=${assistantSessions}
            assistantCurrentSessionId=${assistantCurrentSessionId}
            setAssistantCurrentSessionId=${setAssistantCurrentSessionId}
            currentAssistantProject=${currentAssistantProject}
            handleAssistantScopeChange=${handleAssistantScopeChange}
            handleCreateSession=${handleCreateSession}
            handleRenameSession=${handleRenameSession}
            handleDeleteSession=${handleDeleteSession}
            messageArea=${assistantMessageArea}
            tasksData=${tasksData}
            tasksStats=${tasksStats}
            tasksConnected=${tasksConnected}
            tasksQueuedTasks=${tasksQueuedTasks}
            tasksRunningTasks=${tasksRunningTasks}
            tasksCompletedTasks=${tasksCompletedTasks}
            tasksRefresh=${tasksRefresh}
            usageProjects=${usageProjects}
            usageFilters=${usageFilters}
            setUsageFilters=${setUsageFilters}
            usageStats=${usageStats}
            usageCalls=${usageCalls}
            usageTotal=${usageTotal}
            usagePage=${usagePage}
            setUsagePage=${setUsagePage}
            usagePageSize=${usagePageSize}
            usageLoading=${usageLoading}
            pushToast=${pushToast}
        />
    `;

    if (isLanding) {
        return html`
            <${LandingPage}
                onEnter=${() => navigate({ kind: ROUTE_KIND.PROJECTS })}
                onAssistant=${() => navigate({ kind: ROUTE_KIND.ASSISTANT })}
                onUsage=${() => navigate({ kind: ROUTE_KIND.USAGE })}
            />
        `;
    }

    const totalCalls = usageStats?.total_count || 0;
    const headerActions = html`
        <${Button}
            size="sm"
            variant="outline"
            onClick=${() => setShowCreateModal(true)}
        >
            新建项目
        <//>
    `;

    return html`
        <${AppShell}
            route=${route}
            dashboardKind=${dashboardKind}
            selectedProjectItem=${selectedProjectItem}
            projectsCount=${projects.length}
            totalCalls=${totalCalls}
            onNavigate=${navigate}
            onToggleAssistantPanel=${toggleAssistantPanel}
            headerActions=${headerActions}
        >
            ${dashboardContent}
        <//>

            <${AssistantFloatingMount}
                open=${assistantPanelOpen}
                onClose=${() => setAssistantPanelOpen(false)}
                onNavigate=${navigate}
                currentAssistantProject=${currentAssistantProject}
                projects=${projects}
                onAssistantScopeChange=${handleAssistantScopeChange}
                assistantCurrentSessionId=${assistantCurrentSessionId}
                setAssistantCurrentSessionId=${setAssistantCurrentSessionId}
                assistantSessions=${assistantSessions}
                onCreateSession=${handleCreateSession}
                messageArea=${assistantMessageArea}
            />

            <${CreateProjectModal}
                open=${showCreateModal}
                createForm=${createForm}
                setCreateForm=${setCreateForm}
                creatingProject=${creatingProject}
                onClose=${() => setShowCreateModal(false)}
                onSubmit=${handleCreateProject}
            />

            <${AssistantSessionDialog}
                open=${sessionDialogOpen}
                mode=${sessionDialogMode}
                title=${sessionDialogTitle}
                setTitle=${setSessionDialogTitle}
                submitting=${sessionDialogSubmitting}
                onClose=${closeSessionDialog}
                onSubmit=${submitSessionDialog}
            />

            <${AssistantDeleteSessionDialog}
                open=${deleteDialogOpen}
                sessionTitle=${deleteDialogSessionTitle}
                submitting=${deleteDialogSubmitting}
                onClose=${closeDeleteDialog}
                onConfirm=${confirmDeleteSession}
            />

            <${AppToast} toast=${toast} />
    `;
}

const rootElement = document.getElementById("app-root");
if (rootElement) {
    createRoot(rootElement).render(html`<${App} />`);
}
