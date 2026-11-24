
import { useParams } from "wouter";
import { useProjectsStore } from "@/stores/projects-store";
import { isDemoProject } from "./demo-project";

export function useDemoWorkbench(): boolean {
  const routeProjectName = useParams<{ projectName?: string }>().projectName;
  const storeDemoMode = useProjectsStore((s) => isDemoProject(s.currentProjectName));
  return routeProjectName ? isDemoProject(routeProjectName) : storeDemoMode;
}
