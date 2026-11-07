/** Reference cost: currency → amount mapping. */
export type CostBreakdown = Record<string, number>;

/** Costs broken down by type */
export interface CostByType {
  image?: CostBreakdown;
  video?: CostBreakdown;
  audio?: CostBreakdown;
  characters?: CostBreakdown;
  scenes?: CostBreakdown;
  props?: CostBreakdown;
  products?: CostBreakdown;
  unassigned?: CostBreakdown;
}

/** Costs for a single segment */
export interface SegmentCost {
  segment_id: string;
  duration_seconds: number;
  estimate: { image: CostBreakdown; video: CostBreakdown; audio?: CostBreakdown };
  actual: { image: CostBreakdown; video: CostBreakdown; audio?: CostBreakdown };
}

/** Costs for one episode */
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

/** Unpriced estimate items or calls excluded from costs, grouped by model. `provider` is the provider ID (`custom-<id>` for custom providers). */
export interface UnpricedModel {
  call_type: "image" | "video" | "audio";
  provider: string;
  provider_name: string;
  model: string;
  count: number;
}

/** Cost estimation API response */
export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo; audio?: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
  /** Unpriced parts on both sides excluded from totals; when empty, the totals are complete. */
  unpriced: { estimate: UnpricedModel[]; actual: UnpricedModel[] };
  /** The project has generated content without successful local media call records (for example, an imported project), so spending cannot be calculated. */
  missing_local_calls: boolean;
}
