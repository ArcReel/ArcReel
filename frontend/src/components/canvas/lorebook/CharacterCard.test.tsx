import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterCard } from "./CharacterCard";
import { useAppStore } from "@/stores/app-store";

vi.mock("@/components/canvas/timeline/VersionTimeMachine", () => ({
  VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
}));

describe("CharacterCard", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    Object.defineProperty(globalThis.URL, "createObjectURL", {
      writable: true,
      value: vi.fn(() => "blob:character-ref"),
    });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", {
      writable: true,
      value: vi.fn(),
    });
  });

  it("renders existing saved reference image", () => {
    render(
      <CharacterCard
        name="Hero"
        character={{
          description: "hero desc",
          voice_style: "warm",
          reference_image: "characters/refs/Hero.png",
        }}
        projectName="demo"
        onSave={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );

    expect(screen.getByAltText("Hero reference image")).toHaveAttribute(
      "src",
      "/api/v1/files/demo/characters/refs/Hero.png",
    );
  });

  it("keeps selected reference file until save and submits it in the payload", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={onSave}
        onGenerate={vi.fn()}
      />,
    );

    const fileInput = container.querySelector("input[type='file']");
    expect(fileInput).not.toBeNull();

    const file = new File(["ref"], "hero.png", { type: "image/png" });
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });

    expect(screen.getByText("Pending save reference image")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("Hero", {
        description: "hero desc",
        voiceStyle: "warm",
        referenceFile: file,
      });
    });
  });

  it("auto-resizes the description textarea as content grows", async () => {
    render(
      <CharacterCard
        name="Hero"
        character={{ description: "hero desc", voice_style: "warm" }}
        projectName="demo"
        onSave={vi.fn().mockResolvedValue(undefined)}
        onGenerate={vi.fn()}
      />,
    );

    const textarea = screen.getByPlaceholderText("Enter character description...");
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      value: 128,
    });

    fireEvent.change(textarea, { target: { value: "hero desc with more lines" } });

    await waitFor(() => {
      expect(textarea).toHaveStyle({ height: "128px" });
    });
  });
});
