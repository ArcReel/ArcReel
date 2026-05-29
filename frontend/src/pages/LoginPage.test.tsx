import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router, useLocation, useSearch } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LoginPage } from "@/pages/LoginPage";
import { useAuthStore } from "@/stores/auth-store";

// 探针组件：把当前 wouter location 渲染出来，响应式读取回跳目标。
// useLocation 只返回 pathname，query 要从 useSearch 取，否则 ?tab=scene 这类
// 回跳参数会被探针漏掉。
function LocationProbe() {
  const [location] = useLocation();
  const search = useSearch();
  return <div data-testid="location">{search ? `${location}?${search}` : location}</div>;
}

function renderLoginAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <LoginPage />
      <LocationProbe />
    </Router>,
  );
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
    const { container } = renderLoginAt("/login?from=%2Fapp%2Fprojects%2Fdemo%3Ftab%3Dscene");
    submitLogin(container);
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/app/projects/demo?tab=scene");
    });
  });

  it("falls back to /app/projects when ?from is an unsafe open-redirect target", async () => {
    const { container } = renderLoginAt("/login?from=https%3A%2F%2Fevil.com%2Fapp%2Fx");
    submitLogin(container);
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/app/projects");
    });
  });

  it("falls back to /app/projects when no ?from is present", async () => {
    const { container } = renderLoginAt("/login");
    submitLogin(container);
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/app/projects");
    });
  });
});
