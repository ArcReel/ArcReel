import type { ObjectStorageAddressingStyle } from "@/types";

export type ObjectStoragePresetId =
  | "aliyun-oss"
  | "tencent-cos"
  | "volcengine-tos"
  | "cloudflare-r2"
  | "qiniu-kodo"
  | "aws-s3"
  | "minio"
  | "custom";

export interface ObjectStoragePreset {
  id: ObjectStoragePresetId;
  labelKey: string;
  hintKey: string;
  defaultRegion: string;
  endpointTemplate: string;
  regionPlaceholder: string;
  endpointPlaceholder: string;
  bucketPlaceholder: string;
  defaultAddressingStyle: ObjectStorageAddressingStyle;
}

export const OBJECT_STORAGE_PRESETS: readonly ObjectStoragePreset[] = [
  {
    id: "aliyun-oss",
    labelKey: "object_storage_preset_aliyun_oss",
    hintKey: "object_storage_preset_hint_aliyun_oss",
    defaultRegion: "cn-hangzhou",
    endpointTemplate: "https://oss-{region}.aliyuncs.com",
    regionPlaceholder: "cn-hangzhou",
    endpointPlaceholder: "https://oss-cn-hangzhou.aliyuncs.com",
    bucketPlaceholder: "my-arcreel-bucket",
    defaultAddressingStyle: "virtual",
  },
  {
    id: "tencent-cos",
    labelKey: "object_storage_preset_tencent_cos",
    hintKey: "object_storage_preset_hint_tencent_cos",
    defaultRegion: "ap-guangzhou",
    endpointTemplate: "https://cos.{region}.myqcloud.com",
    regionPlaceholder: "ap-guangzhou",
    endpointPlaceholder: "https://cos.ap-guangzhou.myqcloud.com",
    bucketPlaceholder: "my-bucket-1250000000",
    defaultAddressingStyle: "virtual",
  },
  {
    id: "volcengine-tos",
    labelKey: "object_storage_preset_volcengine_tos",
    hintKey: "object_storage_preset_hint_volcengine_tos",
    defaultRegion: "cn-beijing",
    endpointTemplate: "https://tos-s3-{region}.volces.com",
    regionPlaceholder: "cn-beijing",
    endpointPlaceholder: "https://tos-s3-cn-beijing.volces.com",
    bucketPlaceholder: "my-arcreel-bucket",
    defaultAddressingStyle: "virtual",
  },
  {
    id: "cloudflare-r2",
    labelKey: "object_storage_preset_cloudflare_r2",
    hintKey: "object_storage_preset_hint_cloudflare_r2",
    defaultRegion: "auto",
    endpointTemplate: "https://<account-id>.r2.cloudflarestorage.com",
    regionPlaceholder: "auto",
    endpointPlaceholder: "https://<account-id>.r2.cloudflarestorage.com",
    bucketPlaceholder: "my-r2-bucket",
    defaultAddressingStyle: "auto",
  },
  {
    id: "qiniu-kodo",
    labelKey: "object_storage_preset_qiniu_kodo",
    hintKey: "object_storage_preset_hint_qiniu_kodo",
    defaultRegion: "cn-east-1",
    endpointTemplate: "https://s3.{region}.qiniucs.com",
    regionPlaceholder: "cn-east-1",
    endpointPlaceholder: "https://s3.cn-east-1.qiniucs.com",
    bucketPlaceholder: "my-kodo-space",
    defaultAddressingStyle: "auto",
  },
  {
    id: "aws-s3",
    labelKey: "object_storage_preset_aws_s3",
    hintKey: "object_storage_preset_hint_aws_s3",
    defaultRegion: "us-east-1",
    endpointTemplate: "https://s3.{region}.amazonaws.com",
    regionPlaceholder: "us-east-1",
    endpointPlaceholder: "https://s3.us-east-1.amazonaws.com",
    bucketPlaceholder: "my-s3-bucket",
    defaultAddressingStyle: "auto",
  },
  {
    id: "minio",
    labelKey: "object_storage_preset_minio",
    hintKey: "object_storage_preset_hint_minio",
    defaultRegion: "us-east-1",
    endpointTemplate: "http://127.0.0.1:9000",
    regionPlaceholder: "us-east-1",
    endpointPlaceholder: "http://127.0.0.1:9000",
    bucketPlaceholder: "arcreel",
    defaultAddressingStyle: "path",
  },
  {
    id: "custom",
    labelKey: "object_storage_preset_custom",
    hintKey: "object_storage_preset_hint_custom",
    defaultRegion: "",
    endpointTemplate: "",
    regionPlaceholder: "us-east-1 / auto",
    endpointPlaceholder: "https://s3.example.com",
    bucketPlaceholder: "my-bucket",
    defaultAddressingStyle: "auto",
  },
];

export function getObjectStoragePreset(id: ObjectStoragePresetId): ObjectStoragePreset {
  return OBJECT_STORAGE_PRESETS.find((preset) => preset.id === id) ?? OBJECT_STORAGE_PRESETS[OBJECT_STORAGE_PRESETS.length - 1];
}

export function buildPresetEndpoint(preset: ObjectStoragePreset, region: string): string {
  if (!preset.endpointTemplate) return "";
  const effectiveRegion = region.trim() || preset.defaultRegion;
  return preset.endpointTemplate.replace("{region}", effectiveRegion);
}

export function hasEndpointPlaceholder(endpoint: string): boolean {
  return /<[^>]+>/.test(endpoint);
}

export function detectPresetForEndpoint(endpoint: string | undefined): ObjectStoragePresetId {
  if (!endpoint) return "custom";
  const lower = endpoint.trim().toLowerCase();
  if (!lower) return "custom";
  if (lower.includes(".aliyuncs.com")) return "aliyun-oss";
  if (lower.includes(".myqcloud.com")) return "tencent-cos";
  if (lower.includes(".volces.com") || lower.includes(".ivolces.com") || lower.includes(".bytepluses.com") || lower.includes(".ibytepluses.com")) {
    return "volcengine-tos";
  }
  if (lower.includes(".r2.cloudflarestorage.com")) return "cloudflare-r2";
  if (lower.includes(".qiniucs.com")) return "qiniu-kodo";
  if (lower.includes(".amazonaws.com")) return "aws-s3";
  if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:\/|$)/.test(lower)) return "minio";
  return "custom";
}
