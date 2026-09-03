import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Shirt } from "lucide-react";
import { API, CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivativeResourceId } from "@/api";
import { enqueueCharacterDerivative } from "@/actions/generation";
import { ImageEditButton } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import type { CharacterDerivativeStatus } from "@/types";

interface CharacterDerivativeSheetProps {
  projectName: string;
  characterName: string;
  derivativeName: string;
  /** 该衍生的读取视图；状态还没拉到时给 undefined，此处只显示占位。 */
  status?: CharacterDerivativeStatus;
  /** 本体是否已有资产图。衍生是对它的一次编辑，没有本体图就无从生成。 */
  ownerHasSheet: boolean;
  /** 衍生登记侧（改描述/改名/删除）的在途标志：写登记时不放行生成。 */
  busy?: boolean;
  /** 版本回退后重新拉取项目数据。 */
  onRestore?: () => Promise<void> | void;
}

/**
 * 衍生浮层里的资产图一格：图、过期标记，以及重生成 / 图片编辑 / 版本回退三个入口。
 *
 * 与本体资产图共用同一族产物坐标，因此这里直接复用站内既有的编辑与版本组件，只把资源
 * 类型换成衍生自己的那一档。过期指「这张图不再等于本体现在的样子」——本体重生成后它的
 * 全部衍生一起转为过期，重生成该衍生即可消解。
 */
export function CharacterDerivativeSheet({
  projectName,
  characterName,
  derivativeName,
  status,
  ownerHasSheet,
  busy = false,
  onRestore,
}: CharacterDerivativeSheetProps) {
  const { t } = useTranslation("assets");
  // 加载失败按「哪一张图失败了」记，不是这一格失败了（同 RefThumbnail）：重生成 / 编辑 /
  // 回退换掉图之后 key 变了，自然重新试一次，否则占位内容会一直顶到组件卸载。
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const resourceId = derivativeResourceId(characterName, derivativeName);
  const sheetPath = status?.character_sheet ?? "";
  const sheetFp = useProjectsStore((s) => (sheetPath ? s.getAssetFingerprint(sheetPath) : null));
  const sheetKey = sheetPath ? `${sheetPath}#${sheetFp ?? ""}` : null;
  const imgError = errorKey !== null && errorKey === sheetKey;
  const activeIds = useActiveResourceIds("character_derivative", projectName);
  const generating = activeIds.has(resourceId);
  const sheetUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, sheetFp) : null;
  const stale = status?.stale === true;
  const alt = `${characterName}/${derivativeName}`;

  const handleGenerate = async () => {
    // 提交时刻新鲜读复核：面板停留期间 prop 上的占用态更新依赖父组件重渲染，存在感知延迟。
    if (busy || isResourceBusy("character_derivative", projectName, resourceId)) {
      useAppStore.getState().pushToast(t("assets:derivative_busy_hint"), "info");
      return;
    }
    try {
      await enqueueCharacterDerivative(projectName, characterName, derivativeName);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  };

  return (
    <div className="mt-2">
      <div
        className="relative overflow-hidden rounded-lg"
        style={{ border: "1px solid var(--color-hairline-soft)" }}
      >
        <PreviewableImageFrame src={sheetUrl && !imgError ? sheetUrl : null} alt={alt}>
          <AspectFrame ratio="16:9">
            <ImageFlipReveal
              src={sheetUrl && !imgError ? sheetUrl : null}
              alt={alt}
              className="h-full w-full object-contain"
              onError={() => setErrorKey(sheetKey)}
              fallback={
                <div
                  className="flex h-full w-full flex-col items-center justify-center gap-1.5"
                  style={{ color: "var(--color-text-4)" }}
                >
                  <Shirt className="h-6 w-6" />
                  <span className="text-[10px]">
                    {ownerHasSheet ? t("assets:derivative_no_sheet") : t("assets:derivative_owner_sheet_required")}
                  </span>
                </div>
              }
            />
          </AspectFrame>
        </PreviewableImageFrame>
        {stale && (
          <span
            className="absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium"
            style={{ background: "oklch(0.24 0.06 60 / 0.9)", color: "oklch(0.86 0.12 75)" }}
            title={t("assets:derivative_stale_hint")}
          >
            <AlertTriangle className="h-3 w-3" />
            {t("assets:derivative_stale")}
          </span>
        )}
      </div>

      <div className="mt-1.5 flex items-center gap-1">
        <GenerateButton
          onClick={() => void handleGenerate()}
          loading={generating}
          disabled={busy || !ownerHasSheet}
          label={sheetPath ? t("assets:derivative_regenerate") : t("assets:derivative_generate")}
          className="!px-2 !py-1 !text-[11px]"
        />
        <ImageEditButton
          projectName={projectName}
          resourceType="character_derivative"
          resourceId={resourceId}
          hasImage={Boolean(sheetPath)}
          busy={busy || generating}
        />
        <VersionTimeMachine
          projectName={projectName}
          resourceType={CHARACTER_DERIVATIVE_RESOURCE_TYPE}
          resourceId={resourceId}
          onRestore={onRestore}
          iconOnly
          busy={busy || generating}
        />
      </div>
    </div>
  );
}
