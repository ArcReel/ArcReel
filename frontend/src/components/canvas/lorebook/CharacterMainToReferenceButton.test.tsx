import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { CharacterMainToReferenceButton } from "./CharacterMainToReferenceButton";

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
});

it("moves the current card main image to reference and reloads", async () => {
  const move = vi.spyOn(API, "moveCharacterMainToReference").mockResolvedValue({
    success: true,
    project_asset: {},
    source: "global",
    reference_path: "characters/refs/鳄鱼爸爸.png",
  });
  const onReload = vi.fn();
  const user = userEvent.setup();
  render(
    <CharacterMainToReferenceButton
      projectName="demo"
      characterName="鳄鱼爸爸"
      onReload={onReload}
    />,
  );

  await user.click(screen.getByRole("button", { name: "将主图转为参考图" }));

  await waitFor(() => expect(move).toHaveBeenCalledWith("demo", "鳄鱼爸爸"));
  expect(onReload).toHaveBeenCalled();
});
