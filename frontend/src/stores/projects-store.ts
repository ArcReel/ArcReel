import { create } from "zustand";
import type { ProjectData, ProjectSummary, EpisodeScript } from "@/types";

interface ProjectsState {
  // List
  projects: ProjectSummary[];
  projectsLoading: boolean;

  // Current project detail
  currentProjectName: string | null;
  currentProjectData: ProjectData | null;
  currentScripts: Record<string, EpisodeScript>;
  projectDetailLoading: boolean;

  // Create modal
  showCreateModal: boolean;
  creatingProject: boolean;

  // Actions
  setProjects: (projects: ProjectSummary[]) => void;
  setProjectsLoading: (loading: boolean) => void;
  setCurrentProject: (name: string | null, data: ProjectData | null, scripts?: Record<string, EpisodeScript>) => void;
  setProjectDetailLoading: (loading: boolean) => void;
  setShowCreateModal: (show: boolean) => void;
  setCreatingProject: (creating: boolean) => void;
}

export const useProjectsStore = create<ProjectsState>((set) => ({
  projects: [],
  projectsLoading: false,
  currentProjectName: null,
  currentProjectData: null,
  currentScripts: {},
  projectDetailLoading: false,
  showCreateModal: false,
  creatingProject: false,

  setProjects: (projects) => set({ projects }),
  setProjectsLoading: (loading) => set({ projectsLoading: loading }),
  setCurrentProject: (name, data, scripts = {}) =>
    set({ currentProjectName: name, currentProjectData: data, currentScripts: scripts }),
  setProjectDetailLoading: (loading) => set({ projectDetailLoading: loading }),
  setShowCreateModal: (show) => set({ showCreateModal: show }),
  setCreatingProject: (creating) => set({ creatingProject: creating }),
}));
