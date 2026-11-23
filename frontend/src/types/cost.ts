export type CostBreakdown = Record<string, number>;

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

export interface SegmentCost {
  segment_id: string;
  duration_seconds: number;
  estimate: { image: CostBreakdown; video: CostBreakdown; audio?: CostBreakdown };
  actual: { image: CostBreakdown; video: CostBreakdown; audio?: CostBreakdown };
}

export interface EpisodeCost {
  episode: number;
  title: string;
  segments: SegmentCost[];
  totals: { estimate: CostByType; actual: CostByType };
}

export interface ModelInfo {
  provider: string;
  model: string;
}

export interface UnpricedModel {
  call_type: "image" | "video" | "audio";
  provider: string;
  provider_name: string;
  model: string;
  count: number;
}

export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo; audio?: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
  unpriced: { estimate: UnpricedModel[]; actual: UnpricedModel[] };
  missing_local_calls: boolean;
}
