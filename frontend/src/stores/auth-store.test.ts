import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "./auth-store";
import { clearToken, setToken } from "@/utils/auth";

function mockStatus(enabled: boolean) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ enabled }), { status: 200 }),
  );
}

describe("auth-store initialize", () => {
  beforeEach(() => {
    clearToken();
    useAuthStore.setState({
      token: null,
      isAuthenticated: false,
      isLoading: true,
      authEnabled: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    clearToken();
  });

  it("无 token 且后端认证关闭：放行并记录 authEnabled=false", async () => {
    mockStatus(false);
    useAuthStore.getState().initialize();
    await vi.waitFor(() => expect(useAuthStore.getState().isLoading).toBe(false));
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().authEnabled).toBe(false);
  });

  it("已有 token 时仍查询认证状态，记录 authEnabled=false", async () => {
    setToken("t");
    mockStatus(false);
    useAuthStore.getState().initialize();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    await vi.waitFor(() => expect(useAuthStore.getState().authEnabled).toBe(false));
  });

  it("后端认证开启：authEnabled=true，无 token 不放行", async () => {
    mockStatus(true);
    useAuthStore.getState().initialize();
    await vi.waitFor(() => expect(useAuthStore.getState().isLoading).toBe(false));
    expect(useAuthStore.getState().authEnabled).toBe(true);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
