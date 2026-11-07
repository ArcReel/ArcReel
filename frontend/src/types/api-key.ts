/** API Key metadata for list display, without the full key. */
export interface ApiKeyInfo {
  id: number;
  name: string;
  key_prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

/** API Key creation response, including the full key, which is returned only at creation. */
export interface CreateApiKeyResponse {
  id: number;
  name: string;
  key: string;
  key_prefix: string;
  created_at: string;
  expires_at: string | null;
}
