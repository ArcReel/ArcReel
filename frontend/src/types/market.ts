export type MarketSourceStatus =
  | "never_fetched"
  | "ok"
  | "unreachable"
  | "invalid_index"
  | "unsupported_schema";

export type MarketSourceKind = "official" | "custom";

export interface MarketIndexSummary {
  name: string;
  description: string | null;
  homepage: string | null;
}

export interface MarketSourceInfo {
  id: number;
  kind: MarketSourceKind;
  display_name: string;
  address: string;
  index_url: string;
  canonical_key: string;
  is_enabled: boolean;
  position: number;
  status: MarketSourceStatus;
  last_error: string | null;
  /** 最近一次成功刷新（含 304）的时间；从未成功时为 null。 */
  fetched_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  entry_count: number;
  /** 从未成功抓取时为 null。 */
  index: MarketIndexSummary | null;
}

export interface MarketSourceListResponse {
  sources: MarketSourceInfo[];
}

export interface MarketEntry {
  source_id: number;
  source_display_name: string;
  type: string;
  slug: string;
  path: string;
  name: string;
  author: string;
  version: string;
  media_type: string;
  description: string | null;
  homepage: string | null;
  /** 索引里的 icon 相对路径；非 null 时经 icon 代理取图。 */
  icon: string | null;
  min_app_version: string | null;
  /** 无版本要求或读不到应用版本时为 true。 */
  min_app_version_satisfied: boolean;
  installation: MarketEntryInstallation | null;
}

export interface MarketEntryListResponse {
  entries: MarketEntry[];
  /** 当前应用版本；读不到时为 null。 */
  app_version: string | null;
}

/**
 * 已安装端点的市场轴：安装记录版本与索引条目版本字符串不等即可更新；来源被禁用、被删除或条目已从索引
 * 移除即不可用。与本地修改轴 `modified` 互相独立。
 */
export type MarketInstallationState = "current" | "update_available" | "unavailable";

export interface MarketEntryInstallation {
  endpoint_id: number;
  endpoint_key: string;
  endpoint_display_name: string;
  installed_version: string;
  /** 条目就在市场列表里，不会是 unavailable。 */
  state: Exclude<MarketInstallationState, "unavailable">;
  modified: boolean;
}

export interface EndpointInstallation {
  source_key: string;
  source_id: number | null;
  source_display_name: string | null;
  /** 来源已删除时为 null。 */
  source_enabled: boolean | null;
  slug: string;
  installed_version: string;
  installed_at: string;
  state: MarketInstallationState;
  modified: boolean;
}

export interface MarketEntryDetail {
  entry: MarketEntry;
  source: Pick<MarketSourceInfo, "id" | "kind" | "display_name" | "canonical_key" | "is_enabled" | "status" | "fetched_at" | "index">;
  app_version: string | null;
}

/** 官方服务的开关状态；`enabled` 为 false 时市场页不展示任何官方服务元素。 */
export interface OfficialServiceState {
  available: boolean;
  enabled: boolean;
  notice_seen: boolean;
  /** 随请求发往官方服务的实例标识；尚未生成时为 null。 */
  instance_id: string | null;
}

export interface MarketEntryAggregate {
  source_id: number;
  slug: string;
  /** 官方服务取整后的展示值，原样展示。 */
  installs: number;
  rating_count: number;
  /** 评分人数不足官方服务的阈值时为 null，只展示人数。 */
  rating_average: number | null;
}

export type MarketSubmissionStatus = "open" | "merged" | "closed";

export interface MarketSubmission {
  endpoint_id: number;
  endpoint_key: string;
  endpoint_display_name: string;
  type: string;
  slug: string;
  status: MarketSubmissionStatus;
  pr_url: string;
  /** 本次没能从官方服务取回状态，展示的是上次保存的值。 */
  stale: boolean;
}

/** 分享提交的一条校验诊断；`file` 相对条目目录，slug 本身不合规时为空串。 */
export interface MarketSubmissionDiagnostic {
  file: string;
  path: string;
  code: string;
  message: string;
}

export interface MarketSubmissionIcon {
  filename: "icon.png" | "icon.webp" | "icon.svg";
  /** 标准 base64。 */
  content: string;
}

export interface MarketSubmissionDraft {
  endpoint_id: number;
  slug: string;
  icon: MarketSubmissionIcon | null;
}
