import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { AccountCenterCallbackPage } from "@/pages/AccountCenterCallbackPage";
import { useAuthStore } from "@/stores/auth-store";

describe("AccountCenterCallbackPage", () => {
  beforeEach(() => {
    useAuthStore.setState(useAuthStore.getInitialState(), true);
  });

  it("exchanges the opaque ticket once and stores the exact local identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        access_token: "local-token",
        token_type: "bearer",
        user: {
          id: "local-user-id",
          username: "alice",
          role: "user",
          display_name: "Alice",
          identity_source: "account_center",
        },
      }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const memory = memoryLocation({
      path: "/auth/account-center/callback?ticket=opaque-1&return_to=%2Fapp%2Fprojects%2Fdemo",
      record: true,
    });

    render(<Router hook={memory.hook}><AccountCenterCallbackPage /></Router>);

    await waitFor(() => expect(memory.history.at(-1)).toBe("/app/projects/demo"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/account-center/exchange",
      expect.objectContaining({ body: JSON.stringify({ ticket: "opaque-1" }) }),
    );
    expect(useAuthStore.getState()).toMatchObject({
      token: "local-token",
      userId: "local-user-id",
      username: "alice",
      role: "user",
      identitySource: "account_center",
    });
  });
});
