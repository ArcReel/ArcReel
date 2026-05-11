import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PresetIcon } from "@/components/agent/PresetIcon";

describe("PresetIcon", () => {
  it("renders lobehub icon when iconKey known", async () => {
    render(<PresetIcon iconKey="DeepSeek" size={24} />);
    expect(await screen.findByTestId("lobehub-stub-icon")).toBeInTheDocument();
  });

  it("falls back to monogram on unknown iconKey", async () => {
    render(<PresetIcon iconKey="NonExistentBrand" size={24} />);
    await waitFor(() =>
      expect(screen.getByTestId("preset-icon-monogram")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("preset-icon-monogram").textContent).toBe("N");
  });

  it("falls back to monogram for null iconKey", async () => {
    render(<PresetIcon iconKey={null} size={24} />);
    await waitFor(() =>
      expect(screen.getByTestId("preset-icon-monogram")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("preset-icon-monogram").textContent).toBe("?");
  });
});
