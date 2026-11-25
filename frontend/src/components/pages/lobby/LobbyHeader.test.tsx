import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { LobbyHeader } from "@/components/pages/lobby/LobbyHeader";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BRAND } from "@/branding";
import i18n from "@/i18n";
import { useConfigStatusStore } from "@/stores/config-status-store";

function renderLobbyHeader() {
  const location = memoryLocation({ path: "/app/projects", record: true });
  return render(
    <TooltipProvider delay={0}>
      <Router hook={location.hook}>
        <LobbyHeader
          query=""
          onQueryChange={vi.fn()}
          onCreate={vi.fn()}
          onImport={vi.fn()}
          importing={false}
          onOpenDeleted={vi.fn()}
        />
      </Router>
    </TooltipProvider>,
  );
}

describe("LobbyHeader", () => {
  beforeEach(() => {
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
  });

  afterEach(async () => {
    if (i18n.language !== "zh") {
      await i18n.changeLanguage("zh");
    }
  });

  it("renders the documentation external link immediately to the left of Settings with tooltip", async () => {
    const user = userEvent.setup();
    renderLobbyHeader();

    const docsLink = screen.getByRole("link", { name: "使用文档" });
    const settingsLink = screen.getByRole("link", { name: "设置" });

    expect(docsLink).toHaveAttribute("href", `${BRAND.docsUrl}/`);
    expect(docsLink).toHaveAttribute("target", "_blank");
    expect(docsLink).toHaveAttribute("rel", "noopener noreferrer");

    await user.hover(docsLink);
    await waitFor(() => {
      expect(screen.getByText("使用文档")).toBeVisible();
    });

    docsLink.focus();
    await user.tab();
    expect(settingsLink).toHaveFocus();
  });

  it.each([
    ["en", "Documentation"],
    ["vi", "Tài liệu hướng dẫn"],
    ["ru", "Документация"],
  ] as const)("points the documentation link to /en/ when UI language is %s", async (lang, label) => {
    await i18n.changeLanguage(lang);
    renderLobbyHeader();

    const docsLink = screen.getByRole("link", { name: label });
    expect(docsLink).toHaveAttribute("href", `${BRAND.docsUrl}/en/`);
    expect(docsLink).toHaveAttribute("target", "_blank");
    expect(docsLink).toHaveAttribute("rel", "noopener noreferrer");
  });
});
