import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClueStack } from "./ClueStack";
import type { Scene, Prop } from "@/types";

const scenes: Record<string, Scene> = {
  Forest: { description: "deep woods" },
  Castle: { description: "ancient stronghold" },
};

const props: Record<string, Prop> = {
  Sword: { description: "legendary blade" },
  Map: { description: "treasure map" },
};

describe("ClueStack (read-only)", () => {
  it("renders nothing when both scene and prop lists are empty", () => {
    const { container } = render(
      <ClueStack
        sceneNames={[]}
        propNames={[]}
        scenes={scenes}
        props={props}
        projectName="demo"
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders chips for both scenes and props in order (scenes first)", () => {
    render(
      <ClueStack
        sceneNames={["Forest"]}
        propNames={["Sword"]}
        scenes={scenes}
        props={props}
        projectName="demo"
      />,
    );
    expect(screen.getByText("F")).toBeInTheDocument();
    expect(screen.getByText("S")).toBeInTheDocument();
  });

  it("clamps total scene+prop chips to maxShow and shows +N", () => {
    render(
      <ClueStack
        sceneNames={["Forest", "Castle"]}
        propNames={["Sword", "Map"]}
        scenes={scenes}
        props={props}
        projectName="demo"
        maxShow={2}
      />,
    );
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("does not render any edit affordance (no add / remove buttons)", () => {
    render(
      <ClueStack
        sceneNames={["Forest"]}
        propNames={["Sword"]}
        scenes={scenes}
        props={props}
        projectName="demo"
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});
