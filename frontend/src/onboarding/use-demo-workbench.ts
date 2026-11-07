/**
 * Sole predicate for currently opened demo projects.
 *
 * Most workbench controls disable through existing conventions (omit write callbacks ⇒ hide entries). Cross-layer consumers
 * needing demo awareness (SSE connections, sidebar file fetches, New toolbar entries) use this
 * hook derived from stores. Current project names alone decide, automatically clearing after leaving demos without extra switches.
 *
 * StudioWorkspace asynchronously writes store currentProjectName in effects; initial renders after route-parameter changes
 * (lobby entry or demo/real switches, where :projectName changes do not remount components)
 * precede synchronization. Route parameters exist during render, so prefer them when present, preventing every direct
 * hook consumer independently repeating stale-predicate bugs.
 */

import { useParams } from "wouter";
import { useProjectsStore } from "@/stores/projects-store";
import { isDemoProject } from "./demo-project";

/** Whether current workbenches are readonly demos. */
export function useDemoWorkbench(): boolean {
  const routeProjectName = useParams<{ projectName?: string }>().projectName;
  const storeDemoMode = useProjectsStore((s) => isDemoProject(s.currentProjectName));
  return routeProjectName ? isDemoProject(routeProjectName) : storeDemoMode;
}
