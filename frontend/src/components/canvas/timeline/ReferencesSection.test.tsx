import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReferencesSection } from "./ReferencesSection";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

/** 出场资产摘要行：头像堆按引用名取形态，未登记计数是那个引用名登记了没有。 */

const PROJECT = {
  title: "p",
  content_mode: "drama",
  style: "",
  episodes: [],
  characters: {
    阿岚: {
      description: "本体",
      character_sheet: "characters/阿岚.png",
      derivatives: { 战斗装: { description: "重甲", character_sheet: "characters/derivatives/阿岚/战斗装.png" } },
    },
  },
  scenes: {},
  props: {},
} as unknown as ProjectData;

beforeEach(() => {
  useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: PROJECT });
});

function renderSection(characterNames: string[]) {
  return render(
    <ReferencesSection
      projectName="demo"
      contentMode="drama"
      characterNames={characterNames}
      sceneNames={[]}
      propNames={[]}
      onSave={vi.fn()}
    />,
  );
}

describe("ReferencesSection", () => {
  it("shows the derivative's own sheet in the avatar stack", () => {
    renderSection(["阿岚", "阿岚/战斗装"]);

    const sources = screen.getAllByRole("img").map((img) => img.getAttribute("src") ?? "");
    expect(sources.some((src) => src.includes("characters/阿岚.png"))).toBe(true);
    expect(sources.some((src) => src.includes("characters/derivatives/阿岚/战斗装.png"))).toBe(true);
  });

  it("does not count a registered derivative as an unregistered reference", () => {
    renderSection(["阿岚/战斗装"]);

    expect(screen.queryByText("⚠")).not.toBeInTheDocument();
  });

  it("still counts a derivative the character does not have", () => {
    renderSection(["阿岚/夜行衣"]);

    expect(screen.getByText("⚠")).toBeInTheDocument();
  });
});
