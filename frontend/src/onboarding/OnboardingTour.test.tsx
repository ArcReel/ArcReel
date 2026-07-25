import { act, render, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { OnboardingTour } from "./OnboardingTour";

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <OnboardingTour />
    </Router>,
  );
}

function renderWithNavigation(path: string) {
  const { hook, navigate } = memoryLocation({ path });
  const view = render(
    <Router hook={hook}>
      <OnboardingTour />
    </Router>,
  );
  return { ...view, navigate };
}

function popoverTitle(): string | null {
  return document.querySelector(".driver-popover-title")?.textContent ?? null;
}

describe("OnboardingTour", () => {
  beforeEach(() => {
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
    useAuthStore.setState({ isAuthenticated: true });
    vi.spyOn(API, "markOnboardingSeen").mockResolvedValue({ seen: true });
  });

  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("opens the tour on the first visit to the main interface", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects");

    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
  });

  it("stays out of the way once the tour has been seen", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });

    renderAt("/app/projects");

    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(popoverTitle()).toBeNull();
  });

  it("does not run before the user reaches the main interface", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
    useAuthStore.setState({ isAuthenticated: false });

    renderAt("/app/projects");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on the login page", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/login");

    expect(status).not.toHaveBeenCalled();
  });

  it("does not run on an unmatched route (404)", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/some/unknown/path");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on an unmatched route that merely shares the /app prefix", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/application");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on an unregistered sub-path under a single-page app route", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/settings/unknown");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("does not run on an unregistered sub-path inside a project workspace", () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/unknown");

    expect(status).not.toHaveBeenCalled();
    expect(popoverTitle()).toBeNull();
  });

  it("runs on a registered sub-path inside a project workspace", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/characters");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("runs on the project settings page", async () => {
    const status = vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects/my-novel/settings");

    await waitFor(() => expect(status).toHaveBeenCalled());
  });

  it("marks the tour as seen when it is closed, and does not reopen it", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    document.querySelector<HTMLElement>(".driver-popover-close-btn")?.click();

    await waitFor(() => expect(API.markOnboardingSeen).toHaveBeenCalledTimes(1));
    expect(popoverTitle()).toBeNull();
    expect(useOnboardingStore.getState().seen).toBe(true);
  });

  it("replays the tour on request without writing the flag again", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: true });

    renderAt("/app/projects");
    await waitFor(() => expect(API.getOnboardingStatus).toHaveBeenCalled());

    useOnboardingStore.getState().start();

    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
    document.querySelector<HTMLElement>(".driver-popover-close-btn")?.click();

    await waitFor(() => expect(popoverTitle()).toBeNull());
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("keeps its place when the interface language changes mid-tour", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));
    document.querySelector<HTMLElement>(".driver-popover-next-btn")?.click();
    expect(popoverTitle()).toBe("轮到你了");

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(popoverTitle()).toBe("Your turn"));
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("takes the tour down when the user navigates back to the login page mid-tour", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { navigate } = renderWithNavigation("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    act(() => navigate("/login"));

    expect(popoverTitle()).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });

  it("takes the tour down when the mount point unmounts", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { unmount } = renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    unmount();

    expect(document.querySelector(".driver-popover")).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });
});
