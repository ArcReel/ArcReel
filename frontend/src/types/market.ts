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
  fetched_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  entry_count: number;
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
  icon: string | null;
  min_app_version: string | null;
  min_app_version_satisfied: boolean;
  installation: MarketEntryInstallation | null;
}

export interface MarketEntryListResponse {
  entries: MarketEntry[];
  app_version: string | null;
}

export type MarketInstallationState = "current" | "update_available" | "unavailable";

export interface MarketEntryInstallation {
  endpoint_id: number;
  endpoint_key: string;
  endpoint_display_name: string;
  installed_version: string;
  state: Exclude<MarketInstallationState, "unavailable">;
  modified: boolean;
}

export interface EndpointInstallation {
  source_key: string;
  source_id: number | null;
  source_display_name: string | null;
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

export interface OfficialServiceState {
  available: boolean;
  enabled: boolean;
  notice_seen: boolean;
  instance_id: string | null;
}

export interface MarketEntryAggregate {
  source_id: number;
  slug: string;
  installs: number;
  rating_count: number;
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
  stale: boolean;
}

export interface MarketSubmissionDiagnostic {
  file: string;
  path: string;
  code: string;
  message: string;
}

export interface MarketSubmissionIcon {
  filename: "icon.png" | "icon.webp" | "icon.svg";
  content: string;
}

export interface MarketSubmissionDraft {
  endpoint_id: number;
  slug: string;
  icon: MarketSubmissionIcon | null;
}
