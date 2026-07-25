import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useOnboardingStore } from "./onboarding-store";

describe("useOnboardingStore", () => {
  beforeEach(() => {
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  describe("loadStatus", () => {
    it("records the flag returned by the backend", async () => {
      vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });

      await useOnboardingStore.getState().loadStatus();

      expect(useOnboardingStore.getState().seen).toBe(false);
    });

    it("treats a failed lookup as already seen so nothing pops up", async () => {
      vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.spyOn(API, "getOnboardingStatus").mockRejectedValue(new Error("offline"));

      await useOnboardingStore.getState().loadStatus();

      expect(useOnboardingStore.getState().seen).toBe(true);
    });

    it("drops a response that arrives after its signal was aborted", async () => {
      vi.spyOn(API, "getOnboardingStatus").mockResolvedValue({ seen: false });
      const controller = new AbortController();

      const pending = useOnboardingStore.getState().loadStatus({ signal: controller.signal });
      controller.abort();
      await pending;

      expect(useOnboardingStore.getState().seen).toBeNull();
    });
  });

  describe("start", () => {
    it("opens the tour", () => {
      useOnboardingStore.getState().start();

      expect(useOnboardingStore.getState().active).toBe(true);
    });
  });

  describe("exit", () => {
    it("marks the tour as seen on the first exit", async () => {
      const markSeen = vi.spyOn(API, "markOnboardingSeen").mockResolvedValue({ seen: true });
      useOnboardingStore.setState({ seen: false });
      useOnboardingStore.getState().start();

      useOnboardingStore.getState().exit();
      await vi.waitFor(() => expect(markSeen).toHaveBeenCalledTimes(1));

      expect(useOnboardingStore.getState()).toMatchObject({ active: false, seen: true });
    });

    it("writes nothing when the tour is replayed", async () => {
      const markSeen = vi.spyOn(API, "markOnboardingSeen").mockResolvedValue({ seen: true });
      useOnboardingStore.setState({ seen: true });
      useOnboardingStore.getState().start();

      useOnboardingStore.getState().exit();
      await Promise.resolve();

      expect(markSeen).not.toHaveBeenCalled();
      expect(useOnboardingStore.getState().seen).toBe(true);
    });

    it("ignores an exit when no tour is running", () => {
      const markSeen = vi.spyOn(API, "markOnboardingSeen").mockResolvedValue({ seen: true });
      useOnboardingStore.setState({ seen: false });

      useOnboardingStore.getState().exit();

      expect(markSeen).not.toHaveBeenCalled();
    });

    it("keeps the tour closed when marking it seen fails", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.spyOn(API, "markOnboardingSeen").mockRejectedValue(new Error("offline"));
      useOnboardingStore.setState({ seen: false });
      useOnboardingStore.getState().start();

      useOnboardingStore.getState().exit();
      await vi.waitFor(() => expect(warn).toHaveBeenCalled());

      expect(useOnboardingStore.getState().active).toBe(false);
    });
  });
});
