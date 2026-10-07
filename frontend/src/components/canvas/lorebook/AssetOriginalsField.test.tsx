import { describe, expect, it } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useProjectsStore } from "@/stores/projects-store";
import { AssetOriginalsField } from "./AssetOriginalsField";

describe("AssetOriginalsField", () => {
  it("lists thumbnails sized to the tiles and opens the original in the viewer", async () => {
    const user = userEvent.setup();
    useProjectsStore.setState({ assetFingerprints: { "products/a.png": 7 } });
    render(
      <AssetOriginalsField projectName="p" name="水杯" assetType="product" paths={["products/a.png"]} readOnly />,
    );

    const tile = screen.getByRole("button", { name: /水杯/ });
    expect(within(tile).getByRole("img")).toHaveAttribute("src", "/api/v1/files/p/products/a.png?v=7&w=160");

    await user.click(tile);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("img")).toHaveAttribute("src", "/api/v1/files/p/products/a.png?v=7");
  });

  it("requests a wider thumbnail for a single character original", () => {
    useProjectsStore.setState({ assetFingerprints: {} });
    render(<AssetOriginalsField projectName="p" name="阿青" assetType="character" paths={["characters/a.png"]} readOnly />);

    expect(within(screen.getByRole("button", { name: /阿青/ })).getByRole("img")).toHaveAttribute(
      "src",
      "/api/v1/files/p/characters/a.png?w=640",
    );
  });

  it("switches to the new version when the fingerprint arrives after mount", () => {
    useProjectsStore.setState({ assetFingerprints: {} });
    render(<AssetOriginalsField projectName="p" name="阿青" assetType="character" paths={["characters/a.png"]} readOnly />);

    act(() => useProjectsStore.setState({ assetFingerprints: { "characters/a.png": 9 } }));

    expect(within(screen.getByRole("button", { name: /阿青/ })).getByRole("img")).toHaveAttribute(
      "src",
      "/api/v1/files/p/characters/a.png?v=9&w=640",
    );
  });
});
