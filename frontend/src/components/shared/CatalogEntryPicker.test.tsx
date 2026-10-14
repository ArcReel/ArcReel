import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { API } from "@/api";
import type { CatalogEntryHit } from "@/types";
import { CatalogEntryPicker } from "./CatalogEntryPicker";

function hit(over: Partial<CatalogEntryHit> & Pick<CatalogEntryHit, "name" | "display_name">): CatalogEntryHit {
  return { maker: "openai", media_type: "text", discontinued: false, ...over };
}

const CATALOG: CatalogEntryHit[] = [
  hit({ name: "gpt-5.5", display_name: "GPT-5.5" }),
  hit({ name: "gpt-image-2", display_name: "GPT Image 2", media_type: "image" }),
  hit({ name: "kling-v3-omni", display_name: "可灵 v3 Omni", maker: "kuaishou", media_type: "video" }),
  hit({ name: "gpt-4o", display_name: "GPT-4o", discontinued: true }),
];

function renderPicker(props: { mediaType?: CatalogEntryHit["media_type"] } = {}) {
  const onSelect = vi.fn();
  const onOpenChange = vi.fn();
  render(<CatalogEntryPicker open onOpenChange={onOpenChange} onSelect={onSelect} {...props} />);
  return { onSelect, onOpenChange };
}

describe("CatalogEntryPicker", () => {
  let search: MockInstance<typeof API.searchCatalogEntries>;

  beforeEach(() => {
    vi.restoreAllMocks();
    search = vi.spyOn(API, "searchCatalogEntries").mockImplementation(async ({ q = "", mediaType } = {}) => ({
      entries: CATALOG.filter(
        (e) =>
          (!mediaType || e.media_type === mediaType) &&
          (!q || `${e.name} ${e.display_name}`.toLowerCase().includes(q.toLowerCase())),
      ),
    }));
  });

  it("starts on the media type the caller gives, and switching the filter searches again", async () => {
    const user = userEvent.setup();
    renderPicker({ mediaType: "video" });

    expect(await screen.findByRole("option", { name: /可灵 v3 Omni/ })).toBeInTheDocument();
    expect(search).toHaveBeenLastCalledWith({ q: "", mediaType: "video" }, expect.anything());
    expect(screen.queryByRole("option", { name: /GPT-5.5/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "视频" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "全部" }));

    expect(await screen.findByRole("option", { name: /GPT-5.5/ })).toBeInTheDocument();
    expect(search).toHaveBeenLastCalledWith({ q: "", mediaType: undefined }, expect.anything());
  });

  it("searches by the typed name within the selected media type", async () => {
    const user = userEvent.setup();
    renderPicker({ mediaType: "text" });
    await screen.findByRole("option", { name: /GPT-5.5/ });

    await user.type(screen.getByPlaceholderText("搜索模型名称"), "4o");

    await waitFor(() => expect(search).toHaveBeenLastCalledWith({ q: "4o", mediaType: "text" }, expect.anything()));
    expect(await screen.findByRole("option", { name: /GPT-4o/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("option", { name: /GPT-5.5/ })).not.toBeInTheDocument());
  });

  it("says so when nothing matches", async () => {
    const user = userEvent.setup();
    renderPicker();
    await screen.findByRole("option", { name: /GPT-5.5/ });

    await user.type(screen.getByPlaceholderText("搜索模型名称"), "没有这个模型");

    expect(await screen.findByText("模型目录中没有匹配的模型。")).toBeInTheDocument();
  });

  it("hands the chosen entry back and closes, without exposing entry ids", async () => {
    const user = userEvent.setup();
    const { onSelect, onOpenChange } = renderPicker();
    const option = await screen.findByRole("option", { name: /可灵 v3 Omni/ });
    expect(option).toHaveTextContent("快手 · 视频");
    expect(screen.getByRole("dialog")).not.toHaveTextContent("kling-v3-omni");

    await user.click(option);

    expect(onSelect).toHaveBeenCalledExactlyOnceWith(CATALOG[2]);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("marks discontinued entries but still lets the caller pick them", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderPicker({ mediaType: "text" });

    const option = await screen.findByRole("option", { name: /GPT-4o/ });
    expect(option).toHaveTextContent("已下架");
    await user.click(option);

    expect(onSelect).toHaveBeenCalledWith(CATALOG[3]);
  });

  it("keeps the previous results unselectable until the new search returns", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderPicker({ mediaType: "image" });
    const stale = await screen.findByRole("option", { name: /GPT Image 2/ });
    search.mockImplementationOnce(() => new Promise(() => {}));

    await user.click(screen.getByRole("button", { name: "视频" }));

    await waitFor(() => expect(stale).toHaveAttribute("aria-disabled", "true"));
    await user.click(stale);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("keeps the previous results unselectable while the typed query is still debouncing", async () => {
    const user = userEvent.setup();
    renderPicker({ mediaType: "text" });
    const stale = await screen.findByRole("option", { name: /GPT-5.5/ });

    await user.type(screen.getByPlaceholderText("搜索模型名称"), "4o");

    expect(stale).toHaveAttribute("aria-disabled", "true");
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /GPT-4o/ })).not.toHaveAttribute("aria-disabled", "true"),
    );
  });

  it("explains a failed search and retries on request", async () => {
    const user = userEvent.setup();
    search.mockRejectedValueOnce(new Error("boom"));
    renderPicker();

    expect(await screen.findByText("模型目录加载失败，请稍后重试。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByRole("option", { name: /GPT-5.5/ })).toBeInTheDocument();
    expect(screen.queryByText("模型目录加载失败，请稍后重试。")).not.toBeInTheDocument();
  });
});
