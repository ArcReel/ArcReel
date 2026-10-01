import { describe, expect, it } from "vitest";

import i18n from "@/i18n";
import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, EpisodesViewSegment } from "@/types";

import {
  episodesViewPath,
  formatSpoken,
  formatVolume,
  isReservedEpisodeFileName,
  otherEpisodes,
  railFileGroups,
} from "./episodes-view-model";

function segment(overrides: Partial<EpisodesViewSegment>): EpisodesViewSegment {
  return { kind: "unsplit", start: 0, end: 0, text: "", episode: null, gap: false, units: 0, ...overrides };
}

function info(episode: number, overrides: Partial<EpisodesViewEpisode> = {}): EpisodesViewEpisode {
  return {
    episode,
    origin: "whole_source",
    placed: true,
    source_file: "source/a.txt",
    units: 10,
    spoken_seconds: 3,
    first_sentence: "",
    last_sentence: "",
    ...overrides,
  };
}

function meta(episode: number): EpisodeMeta {
  return { episode, title: `E${episode}`, script_file: `scripts/episode_${episode}.json` };
}

function view(overrides: Partial<EpisodesView>): EpisodesView {
  return { unit: "chars", units: 0, cut_units: 0, files: [], episodes: [], unregistered: [], ...overrides };
}

describe("episodes-view-model", () => {
  it("builds the episodes view address with its one-shot query parameters", () => {
    expect(episodesViewPath()).toBe("/episodes");
    expect(episodesViewPath({ upload: "whole_source" })).toBe("/episodes?upload=whole_source");
    expect(episodesViewPath({ episode: 7 })).toBe("/episodes?episode=7");
  });

  it("formats volume in characters or words and spoken length in minutes", () => {
    const t = i18n.getFixedT("zh");
    expect(formatVolume(t, 12345, "chars")).toBe(`${(12345).toLocaleString()} 字`);
    expect(formatVolume(t, 3, "words")).toBe("3 词");
    expect(formatSpoken(t, 20)).toBe("不到 1 分钟");
    expect(formatSpoken(t, 150)).toBe("约 3 分钟");
  });

  it("groups cut episodes by file with the gaps between them and the unsplit tail", () => {
    const layout = view({
      files: [
        {
          source_file: "source/a.txt",
          name: "a.txt",
          original_filename: null,
          missing: false,
          length: 100,
          units: 100,
          cut_units: 60,
          segments: [
            segment({ kind: "episode", episode: 2, units: 30 }),
            segment({ start: 30, gap: true, units: 10 }),
            segment({ kind: "episode", episode: 1, units: 30, start: 40 }),
            segment({ start: 70, units: 30 }),
          ],
        },
        {
          source_file: "source/b.txt",
          name: "b.txt",
          original_filename: null,
          missing: false,
          length: 100,
          units: 50,
          cut_units: 0,
          segments: [segment({ units: 50 })],
        },
      ],
      episodes: [info(1), info(2), info(3, { origin: "own", placed: false, source_file: null })],
    });

    const groups = railFileGroups(layout, [meta(1), meta(2), meta(3)]);

    expect(groups).toHaveLength(1);
    expect(groups[0].file.name).toBe("a.txt");
    expect(groups[0].tailUnits).toBe(30);
    expect(groups[0].rows.map((row) => (row.kind === "episode" ? row.episode.episode : `gap:${row.units}`))).toEqual([
      2,
      "gap:10",
      1,
    ]);
    expect(otherEpisodes(layout, [meta(1), meta(2), meta(3)]).map(({ episode }) => episode.episode)).toEqual([3]);
  });

  it("reserves episode_N file names for episode sources", () => {
    expect(isReservedEpisodeFileName("episode_12.txt")).toBe(true);
    expect(isReservedEpisodeFileName("episode_12.md")).toBe(true);
    expect(isReservedEpisodeFileName("episode_final.txt")).toBe(false);
    expect(isReservedEpisodeFileName("my_episode_1.txt")).toBe(false);
  });
});
