// Active onboarding prefetches settings chunks, which host settingsProviders/settingsAgent anchors;
// lazy anchors appear only after chunks arrive, and bounded onboarding waits require early prefetching.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

const systemConfigPageLoaded = vi.hoisted(() => vi.fn());
const preloaded = vi.hoisted(() => vi.fn());

// Effects decide prefetch synchronously; wrap preload to record calls without waiting for chunk evaluation to assert No prefetch.
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
  // Module evaluation proves settings chunks were fetched.
  systemConfigPageLoaded();
  return { SystemConfigPage: () => <div data-testid="system-config-page" /> };
});

vi.mock("@/components/pages/ProjectsPage", () => ({
  ProjectsPage: () => <div data-testid="projects-page" />,
}));

vi.mock("@/onboarding/OnboardingTour", () => ({
  OnboardingTour: () => null,
}));

/** Every case gets fresh module registries, resetting lazy-request caches/module evaluation. */
async function renderProjectsWithTour(active: boolean) {
  const [{ AppRoutes }, { useAuthStore }, { useConfigStatusStore }, { useOnboardingStore }] = await Promise.all([
    import("@/router"),
    import("@/stores/auth-store"),
    import("@/stores/config-status-store"),
    import("@/stores/onboarding-store"),
  ]);
  useAuthStore.setState({ isAuthenticated: true, isLoading: false });
  // Short-circuit ConfigStatusLoader fetch(), avoiding unmocked endpoints.
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
