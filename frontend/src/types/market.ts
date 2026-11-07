/** Refresh status of a market source. */
export type MarketSourceStatus =
  | "never_fetched"
  | "ok"
  | "unreachable"
  | "invalid_index"
  | "unsupported_schema";

export type MarketSourceKind = "official" | "custom";

/** Top-level snapshot index information. */
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
  /** Time of the latest successful refresh (including 304); null when none has succeeded. */
  fetched_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  entry_count: number;
  /** Null when no fetch has succeeded. */
  index: MarketIndexSummary | null;
}

export interface MarketSourceListResponse {
  sources: MarketSourceInfo[];
}

/** One merged entry list item: index entry fields plus its source. */
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
  /** Relative icon path from the index; when non-null, the image is fetched through the icon proxy. */
  icon: string | null;
  min_app_version: string | null;
  /** True when no version requirement exists or the app version cannot be read. */
  min_app_version_satisfied: boolean;
  installation: MarketEntryInstallation | null;
}

export interface MarketEntryListResponse {
  entries: MarketEntry[];
  /** Current app version; null when unreadable. */
  app_version: string | null;
}

/**
 * Market axis for an installed endpoint: an update is available whenever installed and indexed version strings differ; unavailable when the source is disabled or deleted, or the entry is
 * removed from the index. Independent of the local modification axis `modified`.
 */
export type MarketInstallationState = "current" | "update_available" | "unavailable";

export interface MarketEntryInstallation {
  endpoint_id: number;
  endpoint_key: string;
  endpoint_display_name: string;
  installed_version: string;
  /** The entry is present in the market list and cannot be unavailable. */
  state: Exclude<MarketInstallationState, "unavailable">;
  /** The current definition digest differs from the digest at installation. */
  modified: boolean;
}

export interface EndpointInstallation {
  source_key: string;
  source_id: number | null;
  source_display_name: string | null;
  /** Null when the source has been deleted. */
  source_enabled: boolean | null;
  slug: string;
  installed_version: string;
  installed_at: string;
  state: MarketInstallationState;
  /** The current definition digest differs from the digest at installation. */
  modified: boolean;
}

export interface MarketEntryDetail {
  entry: MarketEntry;
  source: Pick<MarketSourceInfo, "id" | "kind" | "display_name" | "canonical_key" | "is_enabled" | "status" | "fetched_at" | "index">;
  app_version: string | null;
}

/** Official service switch state; when `enabled` is false, the market page shows no official service elements. */
export interface OfficialServiceState {
  /** The official service address is nonempty. */
  available: boolean;
  /** The address is nonempty and the master switch is on. */
  enabled: boolean;
  /** The initial notice on the market page has been acknowledged. */
  notice_seen: boolean;
  /** Instance identifier sent to the official service with requests; null before generation. */
  instance_id: string | null;
}

/** Install count and ratings of an official market source entry on the official service. */
export interface MarketEntryAggregate {
  source_id: number;
  slug: string;
  /** Display value rounded by the official service, shown unchanged. */
  installs: number;
  rating_count: number;
  /** Null when the rating count is below the official service threshold; only the count is shown. */
  rating_average: number | null;
}

/** Share submission status: under review / accepted / rejected. */
export type MarketSubmissionStatus = "open" | "merged" | "closed";

/** An endpoint's latest submission to the official market. */
export interface MarketSubmission {
  endpoint_id: number;
  endpoint_key: string;
  endpoint_display_name: string;
  type: string;
  slug: string;
  status: MarketSubmissionStatus;
  pr_url: string;
  /** Status could not be fetched from the official service this time; the last saved value is shown. */
  stale: boolean;
}

/** One validation diagnostic for a share submission; `file` is relative to the entry directory, or an empty string when the slug itself is invalid. */
export interface MarketSubmissionDiagnostic {
  file: string;
  path: string;
  code: string;
  message: string;
}

export interface MarketSubmissionIcon {
  filename: "icon.png" | "icon.webp" | "icon.svg";
  /** Standard base64. */
  content: string;
}

export interface MarketSubmissionDraft {
  endpoint_id: number;
  slug: string;
  icon: MarketSubmissionIcon | null;
}
