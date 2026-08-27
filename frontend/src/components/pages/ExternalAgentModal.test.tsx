import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExternalAgentModal } from "@/components/pages/ExternalAgentModal";
import { copyText } from "@/utils/clipboard";

vi.mock("@/utils/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(undefined),
}));

describe("ExternalAgentModal", () => {
  beforeEach(() => {
    vi.mocked(copyText).mockResolvedValue(undefined);
  });

  it("defaults to AI Agent setup and copies an Agent-facing prompt without credentials", async () => {
    const user = userEvent.setup();
    render(<ExternalAgentModal onClose={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "外部智能体接入" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "通过 AI Agent 接入" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const prompt = `帮我接入 ArcReel。请阅读并执行 ${window.location.origin}/agent-installation-guide.md`;
    expect(screen.getByText(prompt)).toBeInTheDocument();
    expect(prompt).not.toContain("arc-");
    expect(screen.queryByRole("link", { name: "查看完整安装指引" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制提示词" }));

    expect(copyText).toHaveBeenLastCalledWith(prompt);
    expect(screen.getByRole("status")).toHaveTextContent("提示词已复制");
  });

  it("shows the single-repository install flow on the manual tab", async () => {
    const user = userEvent.setup();
    render(<ExternalAgentModal onClose={vi.fn()} />);

    await user.click(screen.getByRole("tab", { name: "手动接入" }));

    expect(screen.getByText("npx skills add ArcReel/skills")).toBeInTheDocument();
    expect(screen.getByText("/setup-arcreel-skills")).toBeInTheDocument();
    expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制安装命令" }));
    expect(copyText).toHaveBeenLastCalledWith("npx skills add ArcReel/skills");

    await user.click(screen.getByRole("button", { name: "复制 setup 命令" }));
    expect(copyText).toHaveBeenLastCalledWith("/setup-arcreel-skills");

    await user.click(screen.getByRole("button", { name: "复制 MCP 端点" }));
    expect(copyText).toHaveBeenLastCalledWith(`${window.location.origin}/mcp`);
  });

  it("switches tabs with arrow keys", async () => {
    const user = userEvent.setup();
    render(<ExternalAgentModal onClose={vi.fn()} />);

    const agentTab = screen.getByRole("tab", { name: "通过 AI Agent 接入" });
    agentTab.focus();
    await user.keyboard("{ArrowLeft}");

    expect(screen.getByRole("tab", { name: "手动接入" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "手动接入" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("keeps the modal open when API Key management opens in a new tab", () => {
    const onClose = vi.fn();
    render(<ExternalAgentModal onClose={onClose} />);

    expect(screen.getByRole("link", { name: "创建 API Key" })).toHaveAttribute(
      "href",
      "/app/settings?section=api-keys",
    );
    expect(screen.getByRole("link", { name: "创建 API Key" })).toHaveAttribute("target", "_blank");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("reports clipboard failures with a recovery action", async () => {
    vi.mocked(copyText).mockRejectedValueOnce(new Error("clipboard unavailable"));
    const user = userEvent.setup();
    render(<ExternalAgentModal onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "复制提示词" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("复制失败，请手动选择并复制内容。");
  });
});
