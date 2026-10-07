import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Loader2, Upload } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { buildEntityRevisionKey } from "@/utils/project-changes";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { AssetImageDialog } from "./AssetImageDialog";
import { useTrackWrite } from "./useAssetWrites";

const IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp";

type OriginalsAssetType = "character" | "product";

interface AssetOriginalsFieldProps {
  projectName: string;
  name: string;
  assetType: OriginalsAssetType;
  /** 已上传的原图路径：角色最多一张，商品可以多张。 */
  paths: string[];
  readOnly?: boolean;
  /** 详情里其它写入或生成占用中：上传入口一起禁用。 */
  busy?: boolean;
}

/**
 * 原图：创作者上传的参考图片，生成资产图时作为输入。角色只有一张，上传即替换；商品可以有多张，
 * 上传即追加。上传是立即执行的动作，不进入未保存修改，成功不弹提示。
 */
export function AssetOriginalsField({
  projectName,
  name,
  assetType,
  paths,
  readOnly = false,
  busy = false,
}: AssetOriginalsFieldProps) {
  const { t } = useTranslation(["assets", "dashboard"]);
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [viewing, setViewing] = useState<number | null>(null);
  const track = useTrackWrite();
  // 订阅指纹表本身：只取 getAssetFingerprint 函数时指纹更新不会触发重渲染，地址停在旧版本
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  const multiple = assetType === "product";
  const urls = paths.map((path) => API.getFileUrl(projectName, path, fingerprints[path] ?? null));
  // 列表只显示缩略图（多张 64px 方块，单张 112px 高），点开查看时才拉原图
  const thumbUrls = paths.map((path) =>
    API.getFileUrl(projectName, path, fingerprints[path] ?? null, { width: multiple ? 160 : 640 }),
  );
  const altOf = (index: number) =>
    multiple ? t("assets:original_alt_indexed", { name, index: index + 1 }) : t("assets:original_alt", { name });
  const uploadLabel = multiple
    ? t("dashboard:product_upload_refs")
    : paths.length > 0
      ? t("assets:original_replace")
      : t("assets:original_upload");

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0 || busy || uploading) return;
    if (rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint")) return;
    setUploading(true);
    let uploaded = 0;
    try {
      await track(
        (async () => {
          for (const file of files) {
            await API.uploadFile(projectName, multiple ? "product_ref" : "character_ref", file, name);
            uploaded += 1;
          }
        })(),
      );
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      // 多张上传中途失败时，前面已上传的也要反映出来；一张都没传上就没有可刷新的写入
      if (uploaded > 0) {
        await track(refreshAfterWrite(projectName, t, { invalidateKeys: [buildEntityRevisionKey(assetType, name)] }));
      }
      setUploading(false);
    }
  };

  const disabled = busy || uploading;
  const uploadIcon = uploading ? (
    <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
  ) : multiple ? (
    <ImagePlus aria-hidden data-icon="inline-start" />
  ) : (
    <Upload aria-hidden data-icon="inline-start" />
  );

  return (
    <div className="flex flex-col gap-2">
      {urls.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {thumbUrls.map((url, index) => (
            <li key={paths[index]}>
              <button
                type="button"
                onClick={() => setViewing(index)}
                aria-label={t("assets:view_image", { name: altOf(index) })}
                className="focus-ring block overflow-hidden rounded-md border border-border"
              >
                <img
                  src={url}
                  alt={altOf(index)}
                  className={multiple ? "size-16 object-cover" : "h-28 w-auto max-w-full object-cover"}
                />
              </button>
            </li>
          ))}
        </ul>
      ) : readOnly ? null : (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
          className="focus-ring flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <Upload aria-hidden className="size-4" />}
          {uploadLabel}
        </button>
      )}
      {readOnly || urls.length === 0 ? null : (
        <div>
          <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()} disabled={disabled}>
            {uploadIcon}
            {uploadLabel}
          </Button>
        </div>
      )}
      {readOnly ? null : (
        <input
          ref={inputRef}
          type="file"
          accept={IMAGE_ACCEPT}
          multiple={multiple}
          aria-label={uploadLabel}
          onChange={(e) => void upload(e)}
          className="hidden"
        />
      )}
      <AssetImageDialog
        src={viewing !== null ? (urls[viewing] ?? null) : null}
        title={viewing !== null ? altOf(viewing) : ""}
        alt={viewing !== null ? altOf(viewing) : ""}
        onClose={() => setViewing(null)}
      />
    </div>
  );
}
