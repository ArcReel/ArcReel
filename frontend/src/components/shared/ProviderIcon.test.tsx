import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProviderIcon } from "./ProviderIcon";

describe("ProviderIcon", () => {
  it("renders the Volcengine icon for the bare ark provider", () => {
    render(<ProviderIcon providerId="ark" />);
    expect(screen.getByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  it("renders the Volcengine icon for ark-agent-plan, not the monogram fallback", () => {
    render(<ProviderIcon providerId="ark-agent-plan" />);
    expect(screen.getByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  it("falls back to a monogram badge for an unknown provider", () => {
    render(<ProviderIcon providerId="zeta" />);
    expect(screen.queryByTestId("lobehub-stub-icon")).not.toBeInTheDocument();
    expect(screen.getByText("z")).toBeInTheDocument();
  });

  // Regression: preserve wired built-in provider icons.
  // This never guarantees coverage: providers absent from icon libraries (e.g. Agnes) validly use letter badges,
  // so list wired IDs only; adding iconless providers must not fail these tests.
  const WIRED_BUILTIN_PROVIDER_IDS = [
    "gemini-aistudio",
    "gemini-vertex",
    "grok",
    "ark",
    "ark-agent-plan",
    "dashscope",
    "minimax",
    "openai",
    "vidu",
    "kling",
  ];
  it.each(WIRED_BUILTIN_PROVIDER_IDS)("renders a brand icon for built-in provider %s", (providerId) => {
    render(<ProviderIcon providerId={providerId} />);
    expect(screen.getByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  // Preserve Array.from fallback: astral-plane non-BMP initial characters stay whole rather than half-surrogate pairs.
  it("renders the full first Unicode character in the monogram fallback", () => {
    render(<ProviderIcon providerId="𠮷-provider" />);
    expect(screen.queryByTestId("lobehub-stub-icon")).not.toBeInTheDocument();
    expect(screen.getByText("𠮷")).toBeInTheDocument();
  });
});
