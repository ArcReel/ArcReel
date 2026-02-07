import { useCallback, useEffect, useMemo, useState } from "react";

import { DEFAULT_TAB, ROUTE_KIND } from "../constants.js";

const DEFAULT_CREATE_FORM = {
    name: "",
    title: "",
    contentMode: "narration",
    style: "Photographic",
};

function createDefaultForm() {
    return { ...DEFAULT_CREATE_FORM };
}

export function useProjectsState({
    selectedProject,
    setSelectedProject,
    route,
    projectTab,
    navigate,
    pushToast,
}) {
    const [projects, setProjects] = useState([]);
    const [projectsLoading, setProjectsLoading] = useState(false);

    const [projectDetail, setProjectDetail] = useState(null);
    const [projectDetailLoading, setProjectDetailLoading] = useState(false);

    const [showCreateModal, setShowCreateModal] = useState(false);
    const [creatingProject, setCreatingProject] = useState(false);
    const [createForm, setCreateForm] = useState(createDefaultForm);

    const currentProjectName = useMemo(() => {
        if (route.kind === ROUTE_KIND.WORKSPACE) {
            return route.projectName || selectedProject || "";
        }
        return selectedProject || "";
    }, [route, selectedProject]);

    const selectedProjectItem = useMemo(
        () => projects.find((project) => project.name === currentProjectName) || null,
        [projects, currentProjectName]
    );

    const currentProjectData = projectDetail?.project || null;
    const currentScripts = projectDetail?.scripts || {};

    const loadProjects = useCallback(async () => {
        setProjectsLoading(true);
        try {
            const data = await window.API.listProjects();
            const projectList = data.projects || [];
            setProjects(projectList);

            if (projectList.length === 0) {
                setSelectedProject("");
                setProjectDetail(null);
                if (route.kind === ROUTE_KIND.WORKSPACE) {
                    navigate({ kind: ROUTE_KIND.PROJECTS }, { replace: true });
                }
                return;
            }

            if (!projectList.some((item) => item.name === selectedProject)) {
                setSelectedProject(projectList[0].name);
            }

            if (
                route.kind === ROUTE_KIND.WORKSPACE &&
                !projectList.some((item) => item.name === route.projectName)
            ) {
                navigate(
                    {
                        kind: ROUTE_KIND.WORKSPACE,
                        projectName: projectList[0].name,
                        tab: projectTab,
                    },
                    { replace: true }
                );
            }
        } catch (error) {
            pushToast(`加载项目失败：${error.message}`, "error");
        } finally {
            setProjectsLoading(false);
        }
    }, [navigate, projectTab, pushToast, route.kind, route.projectName, selectedProject]);

    useEffect(() => {
        void loadProjects();
    }, [loadProjects]);

    useEffect(() => {
        if (!currentProjectName) {
            setProjectDetail(null);
            return;
        }

        let canceled = false;

        const run = async () => {
            setProjectDetailLoading(true);
            try {
                const data = await window.API.getProject(currentProjectName);
                if (canceled) {
                    return;
                }
                setProjectDetail(data);
            } catch (error) {
                if (canceled) {
                    return;
                }
                pushToast(`加载项目详情失败：${error.message}`, "error");
                setProjectDetail(null);
            } finally {
                if (!canceled) {
                    setProjectDetailLoading(false);
                }
            }
        };

        void run();
        return () => {
            canceled = true;
        };
    }, [currentProjectName, pushToast]);

    const handleCreateProject = useCallback(
        async (event) => {
            event.preventDefault();

            const name = createForm.name.trim();
            const title = (createForm.title || createForm.name).trim();

            if (!name) {
                pushToast("项目名称不能为空", "error");
                return;
            }

            setCreatingProject(true);
            try {
                await window.API.createProject(name, title, createForm.style, createForm.contentMode);
                pushToast(`项目 ${name} 创建成功`, "success");
                setShowCreateModal(false);
                setCreateForm(createDefaultForm());
                await loadProjects();
                navigate({ kind: ROUTE_KIND.WORKSPACE, projectName: name, tab: DEFAULT_TAB });
            } catch (error) {
                pushToast(`创建失败：${error.message}`, "error");
            } finally {
                setCreatingProject(false);
            }
        },
        [createForm, loadProjects, navigate, pushToast]
    );

    const handleDeleteProject = useCallback(async () => {
        if (!currentProjectName) {
            return;
        }

        const confirmed = window.confirm(`确定删除项目 ${currentProjectName}？此操作不可恢复。`);
        if (!confirmed) {
            return;
        }

        try {
            await window.API.deleteProject(currentProjectName);
            pushToast("项目已删除", "success");
            await loadProjects();
            navigate({ kind: ROUTE_KIND.PROJECTS }, { replace: true });
        } catch (error) {
            pushToast(`删除失败：${error.message}`, "error");
        }
    }, [currentProjectName, loadProjects, navigate, pushToast]);

    const handleRefreshCurrentProject = useCallback(async () => {
        if (!currentProjectName) {
            return;
        }

        try {
            const data = await window.API.getProject(currentProjectName);
            setProjectDetail(data);
        } catch (error) {
            pushToast(`刷新项目失败：${error.message}`, "error");
        }
    }, [currentProjectName, pushToast]);

    return {
        projects,
        projectsLoading,
        projectDetail,
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
    };
}
