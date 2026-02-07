import React from "react";
import htm from "htm";

import { DEFAULT_TAB, ROUTE_KIND } from "../constants.js";
import { AssistantPage } from "../pages/assistant-page.js";
import { ProjectsPage } from "../pages/projects-page.js";
import { UsagePage } from "../pages/usage-page.js";
import { WorkspacePage } from "../pages/workspace-page.js";

const html = htm.bind(React.createElement);

export function DashboardRouterView({
    route,
    selectedProject,
    setSelectedProject,
    projects,
    projectsLoading,
    navigate,
    loadProjects,
    setShowCreateModal,
    currentProjectData,
    currentProjectName,
    projectTab,
    setProjectTab,
    handleRefreshCurrentProject,
    handleDeleteProject,
    projectDetailLoading,
    currentScripts,
    assistantLoadingSessions,
    assistantSessions,
    assistantCurrentSessionId,
    setAssistantCurrentSessionId,
    currentAssistantProject,
    handleAssistantScopeChange,
    handleCreateSession,
    handleRenameSession,
    handleDeleteSession,
    messageArea,
    usageProjects,
    usageFilters,
    setUsageFilters,
    usageStats,
    usageCalls,
    usageTotal,
    usagePage,
    setUsagePage,
    usagePageSize,
    usageLoading,
}) {
    if (route.kind === ROUTE_KIND.PROJECTS) {
        return html`
            <${ProjectsPage}
                projects=${projects}
                projectsLoading=${projectsLoading}
                selectedProject=${selectedProject}
                onSelectProject=${(nextName) => {
                    setSelectedProject(nextName);
                    navigate({ kind: ROUTE_KIND.WORKSPACE, projectName: nextName, tab: DEFAULT_TAB });
                }}
                onRefreshProjects=${() => void loadProjects()}
                onShowCreate=${() => setShowCreateModal(true)}
            />
        `;
    }

    if (route.kind === ROUTE_KIND.WORKSPACE) {
        return html`
            <${WorkspacePage}
                currentProjectData=${currentProjectData}
                currentProjectName=${currentProjectName}
                projectTab=${projectTab}
                onChangeProjectTab=${(nextTab) => {
                    setProjectTab(nextTab);
                    navigate(
                        { kind: ROUTE_KIND.WORKSPACE, projectName: currentProjectName, tab: nextTab },
                        { replace: true }
                    );
                }}
                onRefreshProject=${() => void handleRefreshCurrentProject()}
                onDeleteProject=${handleDeleteProject}
                projectDetailLoading=${projectDetailLoading}
                currentScripts=${currentScripts}
            />
        `;
    }

    if (route.kind === ROUTE_KIND.ASSISTANT) {
        return html`
            <${AssistantPage}
                assistantLoadingSessions=${assistantLoadingSessions}
                assistantSessions=${assistantSessions}
                assistantCurrentSessionId=${assistantCurrentSessionId}
                setAssistantCurrentSessionId=${setAssistantCurrentSessionId}
                currentAssistantProject=${currentAssistantProject}
                projects=${projects}
                onAssistantScopeChange=${handleAssistantScopeChange}
                onCreateSession=${handleCreateSession}
                onRenameSession=${handleRenameSession}
                onDeleteSession=${handleDeleteSession}
                messageArea=${messageArea}
            />
        `;
    }

    return html`
        <${UsagePage}
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
        />
    `;
}
