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
    expect(container.firstChild).toBeNull();
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

  it("does not render any edit affordance (no add / remove buttons)", () => {
    render(
      <AvatarStack
        names={["Hero"]}
        characters={characters}
        projectName="demo"
      />,
    );
    // No buttons of any kind should be inside the stack
    expect(screen.queryByRole("button")).toBeNull();
  });
});
