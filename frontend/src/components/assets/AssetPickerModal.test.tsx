import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AssetPickerModal } from "./AssetPickerModal";
import { API } from "@/api";

// Mock i18next
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (opts) {
        let result = key;
        for (const [k, v] of Object.entries(opts)) {
          result = result.replace(`{{${k}}}`, String(v));
        }
        return result;
      }
      return key;
    },
  }),
}));

const fixtures = [
  { id: "1", type: "character" as const, name: "王小明", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null },
  { id: "2", type: "character" as const, name: "小师妹", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null },
];

describe("AssetPickerModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("multi-selects and calls onImport", async () => {
    vi.spyOn(API, "listAssets").mockResolvedValue({ items: fixtures });
    const onImport = vi.fn();
    render(
      <AssetPickerModal
        type="character"
        existingNames={new Set()}
        onClose={() => {}}
        onImport={onImport}
      />
    );
    await waitFor(() => screen.getByText("王小明"));
    fireEvent.click(screen.getByText("王小明"));
    fireEvent.click(screen.getByText("小师妹"));
    // Click the import button (not the count span)
    const buttons = screen.getAllByRole("button");
    const importButton = buttons.find(b => b.textContent?.includes("import_count") && !b.disabled);
    fireEvent.click(importButton!);
    await waitFor(() => expect(onImport).toHaveBeenCalledWith(["1", "2"]));
  });

  it("disables already-in-project assets", async () => {
    vi.spyOn(API, "listAssets").mockResolvedValue({ items: fixtures });
    render(
      <AssetPickerModal type="character" existingNames={new Set(["王小明"])}
        onClose={() => {}} onImport={vi.fn()} />
    );
    await waitFor(() => screen.getByText("王小明"));
    const card = screen.getByText("王小明").closest("[role='button']") as HTMLElement;
    expect(card).toHaveAttribute("aria-disabled", "true");
  });
});
