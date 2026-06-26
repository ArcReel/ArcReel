import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProviderIcon } from "@/components/ui/ProviderIcon";

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
});
