/** 费用明细：货币 → 金额 映射 */
export type CostBreakdown = Record<string, number>;

/** 按类型拆分的费用 */
export interface CostByType {
  image?: CostBreakdown;
  video?: CostBreakdown;
  audio?: CostBreakdown;
  characters?: CostBreakdown;
  scenes?: CostBreakdown;
  props?: CostBreakdown;
  products?: CostBreakdown;
}

/** 单个 segment 的费用 */
export interface SegmentCost {
  segment_id: string;
  duration_seconds: number;
  estimate: { image: CostBreakdown; video: CostBreakdown; audio?: CostBreakdown };
  actual: { image: CostBreakdown; video: CostBreakdown; audio?: CostBreakdown };
}

/** 单集费用 */
export interface EpisodeCost {
  episode: number;
  title: string;
  segments: SegmentCost[];
  totals: { estimate: CostByType; actual: CostByType };
}

/** 模型信息 */
export interface ModelInfo {
  provider: string;
  model: string;
}

/** 费用估算 API 响应 */
export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo; audio?: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
}
