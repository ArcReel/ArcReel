export type PresentationVariant = "post_production" | "use_tts";
export type PresentationSelection = "current" | "history";
export type PresentationCurrency = "current" | "stale";
export type PresentationResourceType = "videos" | "reference_videos";

export interface ArtifactBasisDescriptor {
  kind: string;
  kind_version: number;
  digest: string;
}

export interface PresentationMedia {
  artifact_path: string;
  version: number;
  selection: PresentationSelection;
  currency: PresentationCurrency | null;
  basis: ArtifactBasisDescriptor | null;
  content_digest: string;
  actual_duration_seconds: number;
}

export interface PresentationVideoTrack extends PresentationMedia {
  start_microseconds: number;
  duration_microseconds: number;
  audio_enabled: boolean;
  gain: number;
}

export interface PresentationNarrationTrack extends PresentationMedia {
  start_microseconds: number;
  duration_microseconds: number;
  gain: number;
}

export interface PresentationSubtitleCue {
  start_microseconds: number;
  duration_microseconds: number;
  text: string;
  owner: "character" | "narrator";
  speaker: string | null;
}

export interface PresentationReadModel {
  schema_version: 1;
  provenance: "verified" | "unavailable";
  episode: number;
  resource_type: PresentationResourceType;
  script_file: string;
  transition_to_next: string;
  subtitle_artifact_path: string | null;
  presentation_artifact_path: string | null;
  persisted: boolean;
  unit_id: string;
  variant: PresentationVariant;
  speech_mode: "silent" | "character_speech" | "narrator_voiceover" | null;
  selection: PresentationSelection;
  currency: PresentationCurrency | null;
  video: PresentationVideoTrack;
  narration_audio: PresentationNarrationTrack | null;
  subtitles: PresentationSubtitleCue[];
  subtitle_basis: ArtifactBasisDescriptor | null;
  presentation_basis: ArtifactBasisDescriptor | null;
  timing: "mechanical" | null;
  subtitles_adjustable: boolean;
  subtitles_webvtt: string | null;
}

export interface PresentationRequestOptions {
  variant?: PresentationVariant;
  videoVersion?: number;
  audioVersion?: number;
  signal?: AbortSignal;
}
