import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { Route, Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { useProjectsStore } from "@/stores/projects-store";
import { DEMO_PROJECT_NAME } from "./demo-project";
import { useDemoWorkbench } from "./use-demo-workbench";

function renderAtProjectRoute(projectName: string) {
  const { hook, navigate } = memoryLocation({ path: `/${projectName}/episodes/1` });
  const view = renderHook(() => useDemoWorkbench(), {
    wrapper: ({ children }) => (
      <Router hook={hook}>
        <Route path="/:projectName" nest>
          {children}
        </Route>
      </Router>
    ),
  });
  return { ...view, navigate };
}

describe("useDemoWorkbench", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  });

  it("switches out of demo mode immediately after navigating from the demo route to a real project, before the store catches up", () => {
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });
    const { result, navigate } = renderAtProjectRoute(DEMO_PROJECT_NAME);
    expect(result.current).toBe(true);

    act(() => navigate("/real-project/episodes/1"));
    // currentProjectName 仍是 store 里的 DEMO_PROJECT_NAME，模拟 StudioWorkspace 的 effect 还没追上路由。
    expect(result.current).toBe(false);
  });

  it("goes read-only immediately after navigating from a real project to the demo route, before the store catches up", () => {
    useProjectsStore.setState({ currentProjectName: "real-project" });
    const { result, navigate } = renderAtProjectRoute("real-project");
    expect(result.current).toBe(false);

    act(() => navigate(`/${DEMO_PROJECT_NAME}/episodes/1`));
    // currentProjectName 仍是 store 里的 "real-project"，同样模拟同一帧内的判定滞后窗口。
    expect(result.current).toBe(true);
  });

  it("falls back to the store-derived value when no route param is available", () => {
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });
    const { hook } = memoryLocation({ path: "/" });
    const { result } = renderHook(() => useDemoWorkbench(), {
      wrapper: ({ children }) => <Router hook={hook}>{children}</Router>,
    });
    expect(result.current).toBe(true);
  });
});
