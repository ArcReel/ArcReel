import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExportScopeDialog } from "./ExportScopeDialog";

vi.mock("@/components/ui/GlassPopover", () => ({
  GlassPopover: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div>{children}</div> : null,
}));

describe("ExportScopeDialog HyperFrames auto-edit", () => {
  it("collects the user instruction and explicit background-music choice", () => {
    const onHyperframesEdit = vi.fn();
    render(
      <ExportScopeDialog
        open
        onClose={vi.fn()}
        onSelect={vi.fn()}
        anchorRef={createRef<HTMLElement>()}
        episodes={[{ episode: 2, title: "田园时光", script_file: "scripts/episode_2.json" }]}
        defaultEpisode={2}
        onHyperframesEdit={onHyperframesEdit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /自动剪辑/ }));
    fireEvent.change(screen.getByLabelText("剪辑 Instruction（可选）"), {
      target: { value: "保持原顺序，前三秒更有冲击力" },
    });
    expect(screen.getByLabelText("自动配置背景音乐")).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "开始自动剪辑" }));

    expect(onHyperframesEdit).toHaveBeenCalledWith(2, {
      narrationDelivery: "post_production",
      instruction: "保持原顺序，前三秒更有冲击力",
      backgroundMusic: true,
    });
  });

  it("allows the user to turn automatic music off", () => {
    const onHyperframesEdit = vi.fn();
    render(
      <ExportScopeDialog
        open
        onClose={vi.fn()}
        onSelect={vi.fn()}
        anchorRef={createRef<HTMLElement>()}
        onHyperframesEdit={onHyperframesEdit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /自动剪辑/ }));
    fireEvent.click(screen.getByLabelText("自动配置背景音乐"));
    fireEvent.click(screen.getByRole("button", { name: "开始自动剪辑" }));

    expect(onHyperframesEdit).toHaveBeenCalledWith(1, {
      narrationDelivery: "post_production",
      instruction: "",
      backgroundMusic: false,
    });
  });
});
