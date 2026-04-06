/** Metadata của API Key (dùng để hiển thị danh sách, không chứa key đầy đủ). */
export interface ApiKeyInfo {
  id: number;
  name: string;
  key_prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

/** Phản hồi tạo API Key (chứa key đầy đủ, chỉ xuất hiện khi tạo). */
export interface CreateApiKeyResponse {
  id: number;
  name: string;
  key: string;
  key_prefix: string;
  created_at: string;
  expires_at: string | null;
}
