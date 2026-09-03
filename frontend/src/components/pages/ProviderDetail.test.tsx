import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { API } from "@/api";
import type { ProviderConfigDetail } from "@/types";
import { ProviderDetail } from "./ProviderDetail";

function detailFor(language: string): ProviderConfigDetail {
  return {
    id: "gemini-aistudio",
    display_name: language === "en" ? "Gemini AI Studio (EN)" : "Gemini AI Studio（中文）",
    description: "",
    status: "ready",
    media_types: ["video"],
    fields: [
      {
        key: "max_workers",
        label: "Max Workers",
        type: "number",
        required: false,
        is_set: true,
        value: "2",
      },
    ],
    supports_base_url: false,
    secret_fields: [],
    secret_field_groups: [],
  };
}

describe("ProviderDetail", () => {
  beforeEach(async () => {
    await act(async () => i18n.changeLanguage("zh"));
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("refetches once on language change without discarding the draft", async () => {
    const getDetail = vi
      .spyOn(API, "getProviderConfig")
      .mockImplementation(() => Promise.resolve(detailFor(i18n.language)));

    render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    expect(getDetail).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    const workers = screen.getByRole("spinbutton", { name: "Max Workers" });
    fireEvent.change(workers, { target: { value: "7" } });

    await act(async () => i18n.changeLanguage("en"));

    await screen.findByText("Gemini AI Studio (EN)");
    await waitFor(() => expect(getDetail).toHaveBeenCalledTimes(2));
    expect(workers).toHaveValue(7);
  });
});
