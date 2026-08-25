import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/auth-store";
import { setToken } from "@/utils/auth";

describe("auth store compatibility", () => {
  beforeEach(() => {
    useAuthStore.setState(useAuthStore.getInitialState(), true);
  });

  it("keeps a legacy local JWT usable as the default admin identity", () => {
    setToken("legacy-token");

    useAuthStore.getState().initialize();

    expect(useAuthStore.getState()).toMatchObject({
      token: "legacy-token",
      userId: "default",
      role: "admin",
      identitySource: "internal",
      isAuthenticated: true,
      isLoading: false,
    });
  });

  it("discovers Account Center without weakening enabled local authentication", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ enabled: true, account_center_enabled: true }),
    } as unknown as Response));

    useAuthStore.getState().initialize();

    await vi.waitFor(() => expect(useAuthStore.getState().isLoading).toBe(false));
    expect(useAuthStore.getState()).toMatchObject({
      accountCenterEnabled: true,
      isAuthenticated: false,
    });
  });
});
