import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { useOnboardingStore } from "@/stores/onboarding-store";

import { GeneralSection } from "./GeneralSection";

describe("GeneralSection", () => {
  beforeEach(() => {
    useOnboardingStore.setState(useOnboardingStore.getInitialState(), true);
  });

  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("选中界面语言后立即切换整页文案", async () => {
    const user = userEvent.setup();
    render(<GeneralSection />);

    const language = screen.getByRole("combobox", { name: "界面语言" });
    expect(language).toHaveTextContent("中文");

    await user.click(language);
    await user.click(await screen.findByRole("option", { name: "English" }));

    expect(await screen.findByRole("combobox", { name: "Interface language" })).toHaveTextContent("English");
    expect(i18n.language).toBe("en");
  });

  it("重看引导会重新开始新手引导", async () => {
    const user = userEvent.setup();
    render(<GeneralSection />);

    await user.click(screen.getByRole("button", { name: "重看引导" }));

    expect(useOnboardingStore.getState().active).toBe(true);
  });
});
