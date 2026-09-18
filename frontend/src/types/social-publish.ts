/** 成片社交分发（Upload-Post）的传输形状，镜像 server/routers/social_publish.py。 */

export interface ConnectedSocialAccount {
  platform: string;
  display_name: string;
  handle: string;
  /** 上游标记该连接需重新授权；带标的账号投递必失败，选择框据此灰掉。 */
  reauth_required: boolean;
}

export interface SocialPublishProfile {
  username: string;
  accounts: ConnectedSocialAccount[];
}

export interface SocialPublishProfilesResponse {
  /** 系统设置里配置的档案名；投递恒定用它。 */
  active_profile: string;
  /** 接受视频投稿的平台白名单；不在其中的已连接账号不进下拉。 */
  video_platforms: string[];
  profiles: SocialPublishProfile[];
}

export interface SocialPublishSubmission {
  request_id: string;
  job_id: string | null;
  scheduled_date: string | null;
  total_platforms: number;
}

export type SocialPlatformStatus =
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "retryable"
  | "skipped";

export interface SocialPlatformOutcome {
  platform: string;
  status: SocialPlatformStatus;
  success: boolean;
  /** 成品贴文地址；排队中、失败、或上游不返回可访问链接时为 null。 */
  url: string | null;
  /** 上游原文的失败原因，不本地化。 */
  error: string | null;
}

export interface SocialPublishProgress {
  request_id: string | null;
  job_id: string | null;
  status: "pending" | "queued" | "processing" | "in_progress" | "completed" | "failed";
  completed: number;
  total: number;
  /** 轮询是否可以停。 */
  terminal: boolean;
  outcomes: SocialPlatformOutcome[];
}

export interface SocialPublishRequest {
  platforms: string[];
  title: string;
  variant?: "post_production" | "use_tts";
  video_version?: number;
  audio_version?: number;
  description?: string;
  /** ISO-8601 定时发布时间；留空即刻投递。 */
  scheduled_date?: string;
  /** IANA 时区名，配合 scheduled_date 使用。 */
  timezone?: string;
}
