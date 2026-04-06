/** Chi tiết chi phí: tiền tệ → số tiền */
export type CostBreakdown = Record<string, number>;

/** Chi phí phân theo loại */
export interface CostByType {
  image?: CostBreakdown;
  video?: CostBreakdown;
  character_and_clue?: CostBreakdown;
}

/** Chi phí của một segment */
export interface SegmentCost {
  segment_id: string;
  duration_seconds: number;
  estimate: { image: CostBreakdown; video: CostBreakdown };
  actual: { image: CostBreakdown; video: CostBreakdown };
}

/** Chi phí một tập */
export interface EpisodeCost {
  episode: number;
  title: string;
  segments: SegmentCost[];
  totals: { estimate: CostByType; actual: CostByType };
}

/** Thông tin mô hình */
export interface ModelInfo {
  provider: string;
  model: string;
}

/** Phản hồi API ước tính chi phí */
export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
}
