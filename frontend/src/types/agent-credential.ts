/**
 * Agent Anthropic credential and preset provider catalog types.
 *
 * Aligned with the Pydantic models in server/routers/agent_config.py.
 */

export interface PresetProvider {
  id: string;
  display_name: string;
  icon_key: string;
  messages_url: string;
  discovery_url: string | null;
  default_model: string;
  suggested_models: string[];
  docs_url: string | null;
  api_key_url: string | null;
  notes: string | null;
  api_key_pattern: string | null;
  is_recommended: boolean;
}

export interface PresetProvidersResponse {
  providers: PresetProvider[];
  custom_sentinel_id: string;
}

/** Agent credential model tiers: the main model and the haiku, sonnet, opus, and subagent routes. */
export type AgentCredentialTier = "model" | "haiku" | "sonnet" | "opus" | "subagent";

/** Pricing status for one tier, resolved by the server against the current model catalog. */
export interface AgentCredentialTierStatus {
  tier: AgentCredentialTier;
  /** The model name actually used by the tier; null means the tier is unset and Claude Code chooses the model. */
  model_name: string | null;
  /** Display name of the matching catalog entry; null when no entry matches. */
  entry_display_name: string | null;
  /** The binding comes from the catalog entry selected by the user for the tier, rather than automatic name matching. */
  manually_bound: boolean;
  /** The tier retains a binding whose entry is no longer in the catalog: matching falls back to the model name, and the binding can still be removed. */
  binding_stale: boolean;
  priced: boolean;
}

export interface AgentCredential {
  id: number;
  preset_id: string;
  display_name: string;
  icon_key: string | null;
  base_url: string;
  api_key_masked: string;
  model: string | null;
  haiku_model: string | null;
  sonnet_model: string | null;
  opus_model: string | null;
  subagent_model: string | null;
  /** Agent credential multiplier applied to the reference cost of each call using this credential; 0 means no cost is counted. */
  multiplier: number;
  /** Pricing status of the five tiers in fixed order: main model, haiku, sonnet, opus, subagent. */
  tiers: AgentCredentialTierStatus[];
  is_active: boolean;
  created_at: string | null;
}

export interface CreateAgentCredentialRequest {
  preset_id: string;
  display_name?: string | null;
  base_url?: string | null;
  /** Mutually exclusive with from_custom_provider_id. */
  api_key?: string;
  /** When provided, the server copies the key from this custom provider (the request must not also include api_key). */
  from_custom_provider_id?: number;
  model?: string | null;
  haiku_model?: string | null;
  sonnet_model?: string | null;
  opus_model?: string | null;
  subagent_model?: string | null;
  multiplier?: number;
  /** Tier → catalog entry ("model maker/official model name"); updates merge by tier, and null removes that tier's binding. */
  tier_bindings?: Partial<Record<AgentCredentialTier, string | null>>;
  activate?: boolean | null;
}

export type UpdateAgentCredentialRequest = Partial<
  Omit<CreateAgentCredentialRequest, "preset_id" | "activate" | "from_custom_provider_id">
>;

export interface ProbeResult {
  success: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
}

export type DiagnosisCode =
  | "openai_compat_only"
  | "auth_failed"
  | "model_not_found"
  | "model_required"
  | "rate_limited"
  | "network"
  | "timeout"
  | "unknown";

export interface SuggestionAction {
  kind: "check_api_key" | "run_discovery" | "see_docs";
  suggested_value: string | null;
}

export interface TestConnectionResponse {
  overall: "ok" | "fail";
  messages_probe: ProbeResult;
  diagnosis: DiagnosisCode | null;
  suggestion: SuggestionAction | null;
  /** Full URL actually requested, matching the preview below the input. */
  messages_url: string;
}

export interface TestConnectionRequest {
  preset_id?: string | null;
  base_url?: string | null;
  api_key: string;
  model?: string | null;
}
