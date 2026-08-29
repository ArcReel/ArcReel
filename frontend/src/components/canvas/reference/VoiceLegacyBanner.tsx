/**
 * 存量过渡横幅：角色参考音频更新后，画布顶部一次性可关闭横幅，提示有片段生成于
 * 设置之前。提示按画布聚合，不做片段级徽章。
 *
 * 计数读时派生（不落盘「早于设置的片段」集合），依据两个机械戳字段：
 * - Character.voice_updated_at：reference_audio 每次变更（含清空/导入）时戳
 * - GeneratedAssets.video_generated_at：视频生成完成时戳，缺省（存量片段）视为
 *   早于任何设置
 *
 * 关闭态落在 Character.voice_notice_dismissed_at（时间戳而非布尔位）：语义是「已确认
 * 到的声音版本」，关闭时写回该角色当时的 voice_updated_at 原值（两侧同源于后端时钟，
 * 不受客户端时钟偏差与 ISO 格式差异影响）。voice_updated_at 晚于它即视为「新变更后
 * 重新出现」，无需额外重置逻辑。横幅按画布（本集 units）聚合展示，关闭时对实际贡献
 * 计数的每个角色分别写回。
 */
import { History, X } from "lucide-react";
import type { Character, CharacterVoiceBinding } from "@/types/project";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types/project";
import { dialogueSpeakers, normalizeAssetName } from "@/utils/reference-mentions";

/**
 * computeVoiceLegacyNotice 所需的最小 unit 形状——同时兼容 narration/drama 的
 * 所有创作类型的 `ReferenceVideoUnit`：完成态 unit
 * 都经同一个 `apply_unit_video_assets` 落盘点戳 `video_generated_at`，横幅判定逻辑
 * 因此不关心具体画布类型。
 */
export interface VoiceNoticeUnit {
  unit_id: string;
  /** 单元正文；发声角色按台词记号的说话人位从中派生，与执行期的音频绑定同一出口。 */
  text?: string | null;
  generated_assets?: { status?: string | null; video_generated_at?: string | null } | null;
}

export interface VoiceLegacyNotice {
  /** 生成于设置之前、且当前未被关闭覆盖的片段数 */
  count: number;
  /** 计数实际涉及的角色名——关闭时需要分别写回这些角色的 dismissed_at */
  characterNames: string[];
}

const EMPTY_NOTICE: VoiceLegacyNotice = { count: 0, characterNames: [] };

/** 两侧时间戳精度/格式不同源（微秒+offset vs 还原版本的秒级 Z），按解析后的实际时刻比较。 */
function toEpochMs(iso: string): number {
  return new Date(iso).getTime();
}

/**
 * 纯函数，独立可测：不依赖 store/hooks。
 *
 * `voiceBinding` 为 prompt（默认）时恒返回空：那条路径压根不挂参考音频，换了参考音频也不改变
 * 已生成视频的声音，横幅会指向一个当前不生效的设置。
 */
export function computeVoiceLegacyNotice(
  units: readonly VoiceNoticeUnit[],
  characters: Record<string, Character>,
  voiceBinding: CharacterVoiceBinding = DEFAULT_CHARACTER_VOICE_BINDING,
): VoiceLegacyNotice {
  if (voiceBinding !== "reference_audio") return EMPTY_NOTICE;
  const staleUnitIds = new Set<string>();
  const characterNames = new Set<string>();
  // characters 的 key 与正文里的名字可能是 NFC/NFD 中的任一方，归一后再查（同
  // `utils/reference-mentions.ts` 的坐标系约定）；采集的 name 用 bucket 的真实 key，
  // 因为消费方按此 name 直接索引 `characters[name]` 做关闭态写回。
  const normalizedCharacters = new Map<string, { key: string; character: Character }>();
  for (const [key, character] of Object.entries(characters)) {
    normalizedCharacters.set(normalizeAssetName(key), { key, character });
  }

  for (const unit of units) {
    if (unit.generated_assets?.status !== "completed") continue;
    const videoGeneratedAt = unit.generated_assets?.video_generated_at;

    // 只看台词记号的说话人：音色只作用于该角色说的台词，出镜但不发声的角色换了音色也
    // 不会让已生成的视频过期。正文缺省是校验层允许的合法状态（迁移问题壳的正文可为空），
    // 当作空串处理，否则整个参考生视频画布会因这一个 unit 崩溃。
    for (const name of dialogueSpeakers(unit.text ?? "")) {
      const found = normalizedCharacters.get(name);
      if (!found) continue;
      const { key: characterKey, character } = found;
      const voiceUpdatedAt = character.voice_updated_at;
      if (!voiceUpdatedAt) continue;
      const voiceUpdatedMs = toEpochMs(voiceUpdatedAt);

      const dismissedAt = character.voice_notice_dismissed_at;
      if (dismissedAt && toEpochMs(dismissedAt) >= voiceUpdatedMs) continue;

      const isStale = !videoGeneratedAt || toEpochMs(videoGeneratedAt) < voiceUpdatedMs;
      if (!isStale) continue;

      staleUnitIds.add(unit.unit_id);
      characterNames.add(characterKey);
    }
  }

  if (staleUnitIds.size === 0) return EMPTY_NOTICE;
  return { count: staleUnitIds.size, characterNames: [...characterNames] };
}

export function VoiceLegacyBanner({
  onDismiss,
  dismissLabel,
  message,
}: {
  onDismiss: () => void;
  dismissLabel: string;
  message: string;
}) {
  return (
    <div
      className="flex shrink-0 items-start gap-2.5 border-b px-5 py-2.5"
      style={{ borderColor: "var(--color-warm-ring)", background: "var(--color-warm-soft)" }}
    >
      <History className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--color-warm)" }} aria-hidden="true" />
      <p className="m-0 flex-1 text-[12px] leading-[1.55] text-[var(--color-text-2)]">{message}</p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={dismissLabel}
        className="focus-ring grid h-5 w-5 shrink-0 place-items-center rounded text-[var(--color-text-3)] hover:bg-[oklch(1_0_0_/_0.06)]"
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}
