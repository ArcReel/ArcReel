// 引导开启时预取设置页 chunk：设置页承载引导锚点（settingsProviders、settingsAgent），
// 懒加载后锚点要等 chunk 到达才出现，引导的锚点等待有上限，只能靠提前拉取兜住。
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

const systemConfigPageLoaded = vi.hoisted(() => vi.fn());
const preloaded = vi.hoisted(() => vi.fn());

// 预取的决定在 effect 里同步发出：包一层 preload 记下调用，不必等 chunk 异步求值才能断言「没有预取」
vi.mock("@/utils/lazy-component", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/lazy-component")>();
  return {
    lazyNamed: ((...args: Parameters<typeof actual.lazyNamed>) => {
      const component = actual.lazyNamed(...args);
      const preload = component.preload;
      component.preload = () => {
        preloaded(args[1]);
        preload();
      };
      return component;
    }) as typeof actual.lazyNamed,
  };
});

vi.mock("@/components/pages/SystemConfigPage", () => {
  // 模块求值即代表设置页 chunk 已被拉取
  systemConfigPageLoaded();
  return { SystemConfigPage: () => <div data-testid="system-config-page" /> };
});

vi.mock("@/components/pages/ProjectsPage", () => ({
  ProjectsPage: () => <div data-testid="projects-page" />,
}));

vi.mock("@/onboarding/OnboardingTour", () => ({
  OnboardingTour: () => null,
}));

/** 每个用例换一份全新的模块表，懒加载组件的请求缓存与模块求值都从头开始。 */
async function renderProjectsWithTour(active: boolean) {
  const [{ AppRoutes }, { useAuthStore }, { useConfigStatusStore }, { useOnboardingStore }] = await Promise.all([
    import("@/router"),
    import("@/stores/auth-store"),
    import("@/stores/config-status-store"),
    import("@/stores/onboarding-store"),
  ]);
  useAuthStore.setState({ isAuthenticated: true, isLoading: false });
  // 让 ConfigStatusLoader 的 fetch() 短路，避免触发未 mock 的接口
  useConfigStatusStore.setState({ initialized: true });
  useOnboardingStore.setState({ active });

  const { hook } = memoryLocation({ path: "/app/projects" });
  render(
    <Router hook={hook}>
      <AppRoutes />
    </Router>,
  );
  expect(await screen.findByTestId("projects-page")).toBeInTheDocument();
}

describe("onboarding chunk preload", () => {
  beforeEach(() => {
    vi.resetModules();
    systemConfigPageLoaded.mockClear();
    preloaded.mockClear();
  });

  it("fetches the settings page chunk while the tour is active, before the user opens settings", async () => {
    await renderProjectsWithTour(true);

    expect(preloaded).toHaveBeenCalledWith("SystemConfigPage");
    await vi.waitFor(() => expect(systemConfigPageLoaded).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("system-config-page")).not.toBeInTheDocument();
  });

  it("leaves the settings page chunk alone when the tour is inactive", async () => {
    await renderProjectsWithTour(false);

    expect(preloaded).not.toHaveBeenCalled();
    expect(systemConfigPageLoaded).not.toHaveBeenCalled();
  });
});
