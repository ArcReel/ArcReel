import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Settings } from "lucide-react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { PageHeader } from "./PageHeader";
import { PageShell } from "./PageShell";
import { PageSidebar, type PageSidebarGroup } from "./PageSidebar";

const GROUPS: PageSidebarGroup[] = [
  {
    id: "system",
    label: "系统",
    items: [{ id: "general", label: "通用", icon: Settings, href: "/app/settings?section=general" }],
  },
];

/** 移动档：`(max-width: 47.999rem)` 匹配，其余断点不匹配。 */
function useMobileViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

function useDesktopViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

function renderShell() {
  const location = memoryLocation({ path: "/app/settings?section=general" });
  render(
    <Router hook={location.hook}>
      <PageShell
        tier="constrained"
        sidebarLabel="设置"
        header={<PageHeader back={{ label: "返回", onClick: () => {} }} title="设置" />}
        sidebar={<PageSidebar label="设置" groups={GROUPS} activeId="general" replace />}
      >
        <p>设置内容</p>
      </PageShell>
    </Router>,
  );
}

describe("PageShell mobile sidebar", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the sidebar inline and hides the menu button on desktop", () => {
    useDesktopViewport();
    renderShell();

    expect(screen.getByRole("navigation", { name: "设置" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开导航菜单" })).not.toBeInTheDocument();
  });

  it("moves the sidebar into a drawer opened from the header on mobile", () => {
    useMobileViewport();
    renderShell();

    // 侧栏不在文档流里，导航只在抽屉打开时出现
    expect(screen.queryByRole("navigation", { name: "设置" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));

    const dialog = screen.getByRole("dialog", { name: "设置" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "通用" })).toBeInTheDocument();
  });

  it("closes the drawer with Escape", () => {
    useMobileViewport();
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));
    expect(screen.getByRole("dialog", { name: "设置" })).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });

    expect(screen.queryByRole("dialog", { name: "设置" })).not.toBeInTheDocument();
  });

  it("does not render a menu button when the page has no sidebar", () => {
    useMobileViewport();
    const location = memoryLocation({ path: "/app/projects" });
    render(
      <Router hook={location.hook}>
        <PageShell tier="full" header={<PageHeader title="项目" />}>
          <p>大厅内容</p>
        </PageShell>
      </Router>,
    );

    expect(screen.queryByRole("button", { name: "打开导航菜单" })).not.toBeInTheDocument();
  });
});
