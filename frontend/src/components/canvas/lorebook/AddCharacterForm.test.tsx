import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AddCharacterForm } from "./AddCharacterForm";

describe("AddCharacterForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(globalThis.URL, "createObjectURL", {
      writable: true,
      value: vi.fn(() => "blob:add-character-ref"),
    });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", {
      writable: true,
      value: vi.fn(),
    });
  });

  it("submits an optional reference file together with the new character", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <AddCharacterForm onSubmit={onSubmit} onCancel={vi.fn()} />,
    );

    fireEvent.change(screen.getByPlaceholderText("角色名称"), {
      target: { value: "Hero" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("角色外貌、性格、背景等描述..."),
      {
        target: { value: "hero desc" },
      },
    );
    fireEvent.change(screen.getByPlaceholderText("可选，例如：温柔但有威严"), {
      target: { value: "warm" },
    });

    const file = new File(["ref"], "hero.png", { type: "image/png" });
    const fileInput = container.querySelector("input[type='file']");
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });

    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith("Hero", "hero desc", "warm", file);
    });
  });
});
