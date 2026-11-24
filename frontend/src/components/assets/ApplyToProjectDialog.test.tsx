import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { Asset } from "@/types/asset";
import { ApplyToProjectDialog } from "./ApplyToProjectDialog";

const ASSET: Asset = {
  id: "library-hero",
  type: "character",
  name: "Hero",
  description: "",
  voice_style: "",
  image_path: null,
  audio_path: null,
  source_project: null,
  updated_at: null,
  derivatives: [],
};

afterEach(() => vi.restoreAllMocks());

it("explains why a busy project asset could not be overwritten and leaves the dialog open for retry", async () => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  vi.spyOn(API, "listProjects").mockResolvedValue({
    projects: [{ name: "demo", title: "Demo", style: "", thumbnail: null, status: {}, last_activity_at: null }],
  });
  const apply = vi.spyOn(API, "applyAssetsToProject").mockResolvedValue({
    succeeded: [],
    skipped: [],
    failed: [{ id: ASSET.id, reason: "resource_busy" }],
  });
  render(<ApplyToProjectDialog asset={ASSET} onClose={onClose} />);

  const dialog = await screen.findByRole("dialog");
  const target = within(dialog).getByRole("combobox", { name: "目标项目" });
  await waitFor(() => expect(target).toBeEnabled());
  await user.click(target);
  await user.click(await screen.findByRole("option", { name: "Demo" }));
  await user.click(within(dialog).getByRole("radio", { name: "覆盖项目里的同类资产" }));
  await user.click(within(dialog).getByRole("button", { name: "应用" }));

  expect(apply).toHaveBeenCalledWith({
    asset_ids: [ASSET.id],
    target_project: "demo",
    conflict_policy: "overwrite",
  });
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "项目资产有任务正在排队或执行，无法覆盖。任务结束后再试。",
  );
  expect(within(dialog).getByRole("button", { name: "应用" })).toBeEnabled();
  expect(onClose).not.toHaveBeenCalled();
});
