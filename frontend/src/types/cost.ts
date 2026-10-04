/** 参考费用：货币 → 金额映射。 */
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
  unassigned?: CostBreakdown;
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

/** 没有价格、未计入费用的预估项或调用，按模型汇总。`provider` 是供应商 id（自定义供应商为 `custom-<id>`）。 */
export interface UnpricedModel {
  call_type: "image" | "video" | "audio";
  provider: string;
  provider_name: string;
  model: string;
  count: number;
}

/** 费用估算 API 响应 */
export interface CostEstimateResponse {
  project_name: string;
  models: { image: ModelInfo; video: ModelInfo; audio?: ModelInfo };
  episodes: EpisodeCost[];
  project_totals: { estimate: CostByType; actual: CostByType };
  /** 两侧没有价格、未计入合计的部分；为空时合计是完整的。 */
  unpriced: { estimate: UnpricedModel[]; actual: UnpricedModel[] };
  /** 项目已有生成出的内容，本机却没有它成功的媒体调用记录（如导入的项目）：已花无法统计。 */
  missing_local_calls: boolean;
}
