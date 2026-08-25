import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { AccountCenterSetupPage } from "@/pages/AccountCenterSetupPage";
import { useAuthStore } from "@/stores/auth-store";

function response(ok: boolean, payload: unknown): Response {
  return { ok, json: vi.fn().mockResolvedValue(payload) } as unknown as Response;
}

describe("AccountCenterSetupPage", () => {
  beforeEach(() => {
    useAuthStore.setState(useAuthStore.getInitialState(), true);
  });

  it("auto-creates the local account and removes the one-time ticket from history", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(true, { username: "alice", display_name: "Alice", roles: ["creator"] }))
      .mockResolvedValueOnce(response(true, {
        access_token: "local-token",
        user: {
          id: "local-user",
          username: "alice",
          role: "user",
          display_name: "Alice",
          identity_source: "account_center",
        },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const memory = memoryLocation({
      path: "/auth/account-center/setup?ticket=opaque-ticket&return_to=%2Fapp%2Fprojects%2Fdemo",
      record: true,
    });
    const { getAllByRole } = render(
      <Router hook={memory.hook}><AccountCenterSetupPage /></Router>,
    );

    await waitFor(() => expect(getAllByRole("button")[0]).toBeEnabled());
    fireEvent.click(getAllByRole("button")[0]);

    await waitFor(() => expect(memory.history.at(-1)).toBe("/app/projects/demo"));
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/auth/account-center/setup",
      expect.objectContaining({
        body: JSON.stringify({ ticket: "opaque-ticket", mode: "auto" }),
      }),
    );
    expect(useAuthStore.getState()).toMatchObject({
      token: "local-token",
      userId: "local-user",
      identitySource: "account_center",
    });
  });

  it("binds only with the submitted existing local credentials", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(true, { username: "remote", display_name: null, roles: ["admin"] }))
      .mockResolvedValueOnce(response(true, {
        access_token: "bound-token",
        user: { id: "existing", username: "local-admin", role: "admin" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const memory = memoryLocation({ path: "/auth/account-center/setup?ticket=bind-ticket", record: true });
    const { container } = render(
      <Router hook={memory.hook}><AccountCenterSetupPage /></Router>,
    );

    await waitFor(() => expect(container.querySelector("#bind-username")).toBeEnabled());
    fireEvent.change(container.querySelector("#bind-username")!, { target: { value: "local-admin" } });
    fireEvent.change(container.querySelector("#bind-password")!, { target: { value: "secret" } });
    fireEvent.submit(container.querySelector("form")!);

    await waitFor(() => expect(memory.history.at(-1)).toBe("/app/projects"));
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/auth/account-center/setup",
      expect.objectContaining({
        body: JSON.stringify({
          ticket: "bind-ticket",
          mode: "bind",
          username: "local-admin",
          password: "secret",
        }),
      }),
    );
  });
});
