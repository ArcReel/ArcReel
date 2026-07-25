import { render, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { beforeEach, describe, expect, it, vi } from "vitest";
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

function popoverTitle(): string | null {
  return document.querySelector(".driver-popover-title")?.textContent ?? null;
}

describe("OnboardingTour", () => {
  beforeEach(() => {
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
    useAuthStore.setState({ isAuthenticated: true });
    vi.spyOn(API, "markOnboardingSeen").mockResolvedValue({ seen: true });
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

  it("takes the tour down when the mount point unmounts", async () => {
    vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

    const { unmount } = renderAt("/app/projects");
    await waitFor(() => expect(popoverTitle()).toBe("欢迎来到 ArcReel"));

    unmount();

    expect(document.querySelector(".driver-popover")).toBeNull();
    expect(API.markOnboardingSeen).not.toHaveBeenCalled();
  });
});
