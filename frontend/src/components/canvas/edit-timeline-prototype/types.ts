// PROTOTYPE（#2752）
import type { ResolvedTimeline } from "./model";
import type { Playback } from "./usePlayback";

export interface VariantProps {
  r: ResolvedTimeline;
  pb: Playback;
  timelineId: string;
  onSelectTimeline: (id: string) => void;
  variant: string;
}
