import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LoginPage } from "@/pages/LoginPage";
import { useAuthStore } from "@/stores/auth-store";

// wouter 的 useLocation/useSearch 钩子只暴露 pathname / search，不暴露 hash，
// 无法用渲染探针断言 #fragment。改用 memoryLocation 的 record 历史：navigate
// 入参被逐字 push 进 history，因此可直接断言登录成功后导航到的完整目标
// （含 query 与 hash），从而锁住回跳链路对 hash 的保留。
function renderLoginAt(path: string) {
  const memory = memoryLocation({ path, record: true });
  const view = render(
    <Router hook={memory.hook}>
      <LoginPage />
    </Router>,
  );
  return { ...view, history: memory.history };
}

// 填表并提交。input id 来自 LoginPage（login-username / login-password），
// 用 id 选择避免依赖 i18n 解析后的 label / 按钮文案。
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

  // 锁住登录成功后对 ?from 的「消费」分支：读取 from → safeReturnPath 校验 → 回跳。
  // 防止以后误改回固定跳转 /app/projects 而主流程回归漏过。
  it("navigates to a valid internal ?from path after successful login", async () => {
    const { container, history } = renderLoginAt("/login?from=%2Fapp%2Fprojects%2Fdemo%3Ftab%3Dscene");
    submitLogin(container);
    await waitFor(() => {
      expect(history.at(-1)).toBe("/app/projects/demo?tab=scene");
    });
  });

  // 锁住 hash 在回跳链路中存活：from 携带 #shot-3，登录后导航目标须保留该锚点。
  // 若源码（AuthGuard / 401 拦截 / safeReturnPath）丢掉 hash，此断言会失败。
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
