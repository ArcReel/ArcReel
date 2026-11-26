import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useRunSettingsStore } from "@/stores/run-settings-store";
import { RunSettings } from "./RunSettings";

beforeEach(() => useRunSettingsStore.setState({ projects: {} }));

it("loads configured chat models only when opened and keeps choices scoped to the project", async () => {
  const fetch = vi.spyOn(API, "getAssistantModelOptions").mockResolvedValue({ models: ["configured-sonnet", "configured-opus"] });
  const user = userEvent.setup();
  const { container } = render(<RunSettings projectName="demo" kind="chat" />);
  expect(fetch).not.toHaveBeenCalled();
  const details = container.querySelector("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "执行模型" })).toBeInTheDocument());
  await user.click(screen.getByRole("combobox", { name: "执行模型" }));
  await user.click(await screen.findByRole("option", { name: "configured-opus" }));
  expect(useRunSettingsStore.getState().projects.demo.chat.model).toBe("configured-opus");
  expect(useRunSettingsStore.getState().projects.other).toBeUndefined();
});
