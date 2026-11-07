import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LoginPage } from "@/components/pages/LoginPage";
import { useAuthStore } from "@/stores/auth-store";

// wouter useLocation/useSearch expose pathname/search only, never hash,
// preventing render-probe #fragment assertions. Use memoryLocation record history: navigate
// arguments push verbatim, allowing complete successful-login target assertions
// including query/hash, preserving return-path fragments.
function renderLoginAt(path: string) {
  const memory = memoryLocation({ path, record: true });
  const view = render(
    <Router hook={memory.hook}>
      <LoginPage />
    </Router>,
  );
  return { ...view, history: memory.history };
}

// Fill/submit forms. Input IDs come from LoginPage (login-username / login-password);
// select IDs without depending on translated labels/button copy.
function submitLogin(container: HTMLElement) {
  fireEvent.change(container.querySelector<HTMLInputElement>("#login-username")!, {
    target: { value: "alice" },
  });
  fireEvent.change(container.querySelector<HTMLInputElement>("#login-password")!, {
    target: { value: "pw" },
  });
  fireEvent.submit(container.querySelector("form")!);
}

describe("LoginPage returnTo consumption", () => {
  beforeEach(() => {
    useAuthStore.setState({
      token: null,
      username: null,
      isAuthenticated: false,
      isLoading: false,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ access_token: "tok-123" }),
      } as unknown as Response),
    );
  });

  // Lock successful-login ?from consumption: read from → safeReturnPath validation → return navigation.
  // Prevent accidental fixed /app/projects navigation escaping main-flow regression coverage.
  it("navigates to a valid internal ?from path after successful login", async () => {
    const { container, history } = renderLoginAt("/login?from=%2Fapp%2Fprojects%2Fdemo%3Ftab%3Dscene");
    submitLogin(container);
    await waitFor(() => {
      expect(history.at(-1)).toBe("/app/projects/demo?tab=scene");
    });
  });

  // Lock hash survival: from includes #shot-3, retained by postlogin targets.
  // Source hash loss in AuthGuard / 401 interception / safeReturnPath fails this assertion.
  it("preserves the URL hash in the return path", async () => {
    const { container, history } = renderLoginAt("/login?from=%2Fapp%2Fprojects%2Fdemo%23shot-3");
    submitLogin(container);
    await waitFor(() => {
      expect(history.at(-1)).toBe("/app/projects/demo#shot-3");
    });
  });

  it("falls back to /app/projects when ?from is an unsafe open-redirect target", async () => {
    const { container, history } = renderLoginAt("/login?from=https%3A%2F%2Fevil.com%2Fapp%2Fx");
    submitLogin(container);
    await waitFor(() => {
      expect(history.at(-1)).toBe("/app/projects");
    });
  });

  it("falls back to /app/projects when no ?from is present", async () => {
    const { container, history } = renderLoginAt("/login");
    submitLogin(container);
    await waitFor(() => {
      expect(history.at(-1)).toBe("/app/projects");
    });
  });
});
