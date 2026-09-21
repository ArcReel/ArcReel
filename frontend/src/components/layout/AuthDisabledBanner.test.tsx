import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { AuthDisabledBanner } from "./AuthDisabledBanner";
import { useAuthStore } from "@/stores/auth-store";

describe("AuthDisabledBanner", () => {
  beforeEach(() => {
    useAuthStore.setState({ authEnabled: null });
  });

  it("认证关闭时渲染常驻提示条", () => {
    useAuthStore.setState({ authEnabled: false });
    render(<AuthDisabledBanner />);
    const banner = screen.getByRole("status");
    expect(banner).toBeInTheDocument();
    expect(banner.querySelector("button")).toBeNull();
  });

  it("认证开启时不渲染", () => {
    useAuthStore.setState({ authEnabled: true });
    render(<AuthDisabledBanner />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("认证状态未知时不渲染", () => {
    render(<AuthDisabledBanner />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
