import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AvatarStack } from "./AvatarStack";
import type { Character } from "@/types";

const characters: Record<string, Character> = {
  Hero: { description: "main protagonist" },
  Villain: { description: "antagonist" },
  Mentor: { description: "guide" },
};

describe("AvatarStack (read-only)", () => {
  it("renders nothing when names is empty", () => {
    const { container } = render(
      <AvatarStack names={[]} characters={characters} projectName="demo" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one chip per visible name", () => {
    render(
      <AvatarStack
        names={["Hero", "Villain"]}
        characters={characters}
        projectName="demo"
      />,
    );
    // chips render as initial-letter spans (no character_sheet provided)
    expect(screen.getByText("H")).toBeInTheDocument();
    expect(screen.getByText("V")).toBeInTheDocument();
  });

  it("clamps to maxShow and renders +N overflow indicator", () => {
    render(
      <AvatarStack
        names={["Hero", "Villain", "Mentor"]}
        characters={characters}
        projectName="demo"
        maxShow={2}
      />,
    );
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("takes the derivative's own sheet for a `本体/衍生` name", () => {
    const withDerivative: Record<string, Character> = {
      Hero: {
        description: "main protagonist",
        character_sheet: "characters/Hero.png",
        derivatives: {
          Armored: { description: "in black armor", character_sheet: "characters/derivatives/Hero/Armored.png" },
        },
      },
    };
    render(
      <AvatarStack
        names={["Hero", "Hero/Armored"]}
        characters={withDerivative}
        projectName="demo"
      />,
    );
    const sources = screen.getAllByRole("img").map((img) => img.getAttribute("src") ?? "");
    expect(sources.some((src) => src.includes("characters/Hero.png"))).toBe(true);
    expect(sources.some((src) => src.includes("characters/derivatives/Hero/Armored.png"))).toBe(true);
  });

  it("falls back to the derivative's initial so sibling forms stay distinguishable", () => {
    const withDerivative: Record<string, Character> = {
      Hero: { description: "main protagonist", derivatives: { Armored: { description: "in black armor" } } },
    };
    render(
      <AvatarStack names={["Hero/Armored"]} characters={withDerivative} projectName="demo" />,
    );
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("does not render any edit affordance (no add / remove buttons)", () => {
    render(
      <AvatarStack
        names={["Hero"]}
        characters={characters}
        projectName="demo"
      />,
    );
    // No buttons of any kind should be inside the stack
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
