/** Cost breakdown: currency → amount mapping */
export type CostBreakdown = Record<string, number>;

/** Cost split by type */
export interface CostByType {
  image?: CostBreakdown;
  video?: CostBreakdown;
  character_and_clue?: CostBreakdown;
}

/** Cost for a single segment */
export interface SegmentCost {
  segment_id: string;
  duration_seconds: number;
  estimate: { image: CostBreakdown; video: CostBreakdown };
  actual: { image: CostBreakdown; video: CostBreakdown };
}

/** Cost for a single episode */
export interface EpisodeCost {
  episode: number;
  title: string;
  segments: SegmentCost[];
  totals: { estimate: CostByType; actual: CostByType };
}

/** Model information */
export interface ModelInfo {
  provider: string;
  model: string;
}

/** Cost estimation API response */
export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
}
