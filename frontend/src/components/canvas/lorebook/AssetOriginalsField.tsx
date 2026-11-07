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
  /** Uploaded original paths: at most one per character, multiple per product. */
  paths: string[];
  readOnly?: boolean;
  /** Other detail writes/generation are occupied; disable uploads together. */
  busy?: boolean;
}

/**
 * Originals are creator-uploaded references for sheet generation. Characters keep one, replaced on upload; products allow multiple,
 * appended on upload. Upload executes immediately outside unsaved edits, without success toasts.
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
  // Subscribe to fingerprint tables themselves; selecting only getAssetFingerprint functions misses updates and retains stale URLs.
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  const multiple = assetType === "product";
  const urls = paths.map((path) => API.getFileUrl(projectName, path, fingerprints[path] ?? null));
  // Lists use thumbnails (64px squares for multiple, 112px height for one); fetch originals only when opening viewers.
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
            // react-doctor-disable-next-line react-doctor/async-await-in-loop -- Write one asset's originals sequentially to preserve selection order/avoid concurrent record mutations; stop on failure and refresh by successful count
            await API.uploadFile(projectName, multiple ? "product_ref" : "character_ref", file, name);
            uploaded += 1;
          }
        })(),
      );
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      // Reflect earlier successes after partial multi-upload failures; zero successes means no writes to refresh.
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
