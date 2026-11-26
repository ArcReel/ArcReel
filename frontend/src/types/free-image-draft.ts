export interface FreeImageDraft {
  id: string;
  prompt: string;
  resolution: string | null;
  provider: string;
  model: string;
  aspect_ratio: string;
  file_path: string;
  created_at: string;
}
