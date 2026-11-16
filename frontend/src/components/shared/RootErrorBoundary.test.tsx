import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RootErrorBoundary } from "./RootErrorBoundary";

function Broken(): never {
  throw new Error("render blew up");
}

describe("RootErrorBoundary", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children while nothing throws", () => {
    render(
      <RootErrorBoundary>
        <p>app content</p>
      </RootErrorBoundary>,
    );

    expect(screen.getByText("app content")).toBeInTheDocument();
  });

  it("shows a crash message with a reload button instead of leaving the page blank", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <RootErrorBoundary>
        <Broken />
      </RootErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("应用遇到了问题");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });
});
