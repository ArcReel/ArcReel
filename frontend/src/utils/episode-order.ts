import type { EpisodeMeta } from "@/types";

type OrderedEpisode = Pick<EpisodeMeta, "episode" | "source_origin" | "source_range">;

function sourcePosition(
  episode: OrderedEpisode,
  files: readonly { source_file: string }[],
): readonly [number, number] | null {
  if (episode.source_origin !== "whole_source") return null;
  const range = episode.source_range;
  const fileIndex = files.findIndex((file) => file.source_file === range?.source_file);
  if (fileIndex < 0 || typeof range?.start !== "number") return null;
  return [fileIndex, range.start];
}

export function episodeMoveCheck(
  episodes: readonly OrderedEpisode[],
  files: readonly { source_file: string }[],
  episode: number,
  after: number | null,
): "ok" | "noop" | "locked" {
  const from = episodes.findIndex((entry) => entry.episode === episode);
  if (from < 0 || after === episode) return "noop";
  const rest = episodes.filter((entry) => entry.episode !== episode);
  const at = after === null ? 0 : rest.findIndex((entry) => entry.episode === after) + 1;
  if ((after !== null && at === 0) || at === from) return "noop";
  const moved = [...rest.slice(0, at), episodes[from], ...rest.slice(at)];
  const positions = moved
    .map((entry) => sourcePosition(entry, files))
    .filter((position): position is readonly [number, number] => position !== null);
  const ordered = positions.every(
    (position, index) =>
      index === 0 ||
      positions[index - 1][0] < position[0] ||
      (positions[index - 1][0] === position[0] && positions[index - 1][1] <= position[1]),
  );
  return ordered ? "ok" : "locked";
}
