import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LazyBoundary } from "./LazyBoundary";

function Broken(): never {
  throw new Error("chunk still 404");
}

describe("LazyBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a load-failed message with a reload button instead of unmounting the app", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <LazyBoundary variant="pane">
        <Broken />
      </LazyBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("页面加载失败");
    // jsdom 不支持真正的刷新，这里只确认入口存在
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("renders nothing on failure for components without their own DOM", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(
      <LazyBoundary variant="none">
        <Broken />
      </LazyBoundary>,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("clears the failure once the user navigates elsewhere", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { hook, navigate } = memoryLocation({ path: "/app/settings" });
    function Page() {
      const [path] = hook();
      return path === "/app/settings" ? <Broken /> : <p>projects</p>;
    }

    render(
      <Router hook={hook}>
        <LazyBoundary variant="screen">
          <Page />
        </LazyBoundary>
      </Router>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();

    act(() => navigate("/app/projects"));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("projects")).toBeInTheDocument();
  });
});
