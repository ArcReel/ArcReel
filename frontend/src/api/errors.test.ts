import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { useProjectsStore } from "@/stores/projects-store";
import { API } from "@/api";
import { useItemLocationsStore } from "@/stores/item-locations-store";
import { locateDiagnosticItems, ScriptEditCommandError, SpeechAdmissionError } from "./errors";

describe("api/errors position formatting without currentScripts", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({
      currentProjectName: "demo",
      currentProjectData: {
        title: "Demo",
        content_mode: "narration",
        style: "",
        characters: {},
        episodes: [{ episode: 2, title: "雨夜", script_file: "scripts/episode_2.json" }],
      },
      currentScripts: {},
    });
  });

  it("formats SpeechAdmissionError using backend-supplied problem.position without loaded scripts", () => {
    const err = new SpeechAdmissionError({
      allowed: false,
      unit_id: "E2U03",
      episode: 2,
      item_id: "E2U03",
      position: 3,
      mode: null,
      problems: [
        {
          code: "needs_replan",
          unit_id: "E2U03",
          episode: 2,
          item_id: "E2U03",
          position: 3,
          locations: [],
          reason: "需重规划",
          action: "请拆分",
        },
      ],
    });
    expect(err.message).toContain("视频单元 3");
    expect(err.message).not.toContain("E2U03");
  });

  it("locates a legacy admission without loading its episode script", async () => {
    const admission = { allowed: false as const, mode: null, unit_id: "U03", problems: [{
      code: "needs_replan" as const, unit_id: "U03", locations: [], reason: "replan", action: "replan",
    }] };
    useItemLocationsStore.getState().reset();
    const locate = vi.spyOn(API, "locateItems").mockResolvedValue({
      items: [{ episode: 2, item_id: "E2U03", position: 6, status: "found" }],
    });
    try {
      await locateDiagnosticItems("demo", admission);
      expect(new SpeechAdmissionError(admission).message).toContain("视频单元 6");
      expect(locate).toHaveBeenCalledTimes(1);
    } finally {
      locate.mockRestore();
      useItemLocationsStore.getState().reset();
    }
  });

  it("formats ScriptEditCommandError using backend-supplied problem.position without loaded scripts", () => {
    const err = new ScriptEditCommandError({
      success: false,
      script: "episode_2.json",
      episode: 2,
      before_revision: "r1",
      revision: "r1",
      affected_ids: [],
      problems: [
        {
          code: "mixed_speech",
          operation_index: 0,
          unit_id: "E2S05",
          episode: 2,
          item_id: "E2S05",
          position: 5,
          locations: [{ path: ["dialogue"], line: 0 }],
          reason: "混有旁白与对白",
          next_action: "请拆分",
        },
      ],
    });
    expect(err.message).toContain("视频单元 5");
    expect(err.message).not.toContain("E2S05");
  });
});
