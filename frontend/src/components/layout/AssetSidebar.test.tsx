import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useDraftCountStore } from "@/stores/draft-count-store";
import type { FreeImageDraft } from "@/types/free-image-draft";
import { AssetSidebar } from "./AssetSidebar";

vi.mock("@/stores/cost-store", () => ({ useCostStore: () => vi.fn() }));
vi.mock("@/onboarding/use-demo-workbench", () => ({ useDemoWorkbench: () => false }));

afterEach(() => { vi.restoreAllMocks(); });

it("shows the draft count, refreshes after a change, and scopes counts to projects", async () => {
  useDraftCountStore.setState({ counts: {}, revisions: {} });
  useProjectsStore.getState().setCurrentProject("first", null);
  const list = vi.spyOn(API, "listFreeImageDrafts");
  list.mockResolvedValueOnce({ items: [{} as FreeImageDraft, {} as FreeImageDraft] });
  render(<AssetSidebar collapsed={false} onCollapsedChange={vi.fn()} />);
  const link = screen.getByRole("link", { name: /草稿库/ });
  await waitFor(() => expect(link).toHaveTextContent("草稿库2"));
  list.mockResolvedValueOnce({ items: [] });
  act(() => useDraftCountStore.getState().invalidate("first"));
  await waitFor(() => expect(link).toHaveTextContent("草稿库0"));
  list.mockResolvedValueOnce({ items: [{} as FreeImageDraft] });
  act(() => useProjectsStore.getState().setCurrentProject("second", null));
  await waitFor(() => expect(link).toHaveTextContent("草稿库1"));
  expect(list.mock.calls.map(([project]) => project)).toEqual(["first", "first", "second"]);
});
