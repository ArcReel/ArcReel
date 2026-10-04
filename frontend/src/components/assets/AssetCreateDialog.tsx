import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Loader2 } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errMsg } from "@/utils/async";
import type { Asset, AssetType } from "@/types/asset";
import { ASSET_TYPE_ICON } from "./asset-type-icons";
import { AssetThumb } from "./AssetThumb";

/** 在资产库里新建一个资产：名称必填，描述、声音风格（角色）与图片可选。 */
export function AssetCreateDialog({
  type,
  onClose,
  onCreated,
}: {
  /** 为 null 时关闭。 */
  type: AssetType | null;
  onClose: () => void;
  onCreated: (asset: Asset) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [shownType, setShownType] = useState(type);
  if (type !== null && type !== shownType) setShownType(type);

  return (
    <Dialog
      open={type !== null}
      onOpenChange={(next) => {
        // 请求在途时不响应 Esc 与遮罩点击，避免资产已建好却没回到列表
        if (!next && !creating) onClose();
      }}
    >
      <DialogContent showCloseButton={!creating}>
        {shownType && (
          <AssetCreateForm
            type={shownType}
            creating={creating}
            setCreating={setCreating}
            onCreated={(asset) => {
              onCreated(asset);
              onClose();
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function AssetCreateForm({
  type,
  creating,
  setCreating,
  onCreated,
}: {
  type: AssetType;
  creating: boolean;
  setCreating: (creating: boolean) => void;
  onCreated: (asset: Asset) => void;
}) {
  const { t } = useTranslation("assets");
  const formId = useId();
  const nameId = useId();
  const descriptionId = useId();
  const voiceId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [voiceStyle, setVoiceStyle] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const Icon = ASSET_TYPE_ICON[type];
  const trimmedName = name.trim();

  useEffect(() => {
    if (!image) return;
    const url = URL.createObjectURL(image);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- object URL 随所选文件创建并在替换或卸载时回收
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!trimmedName || creating) return;
    setCreating(true);
    setError(null);
    try {
      const { asset } = await API.createAsset({
        type,
        name: trimmedName,
        description,
        voice_style: type === "character" ? voiceStyle : "",
        image: image ?? undefined,
      });
      onCreated(asset);
    } catch (err) {
      setError(t("create_failed", { message: errMsg(err) }));
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("create_title", { type: t(`type.${type}`) })}</DialogTitle>
      </DialogHeader>
      <DialogBody>
        <form id={formId} onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <AssetThumb
              imageUrl={image ? previewUrl : null}
              alt={t("image_preview_alt")}
              fallback={<Icon aria-hidden className="size-8" />}
              className="rounded-lg border border-border"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={creating}
                onClick={() => fileRef.current?.click()}
              >
                <ImagePlus aria-hidden data-icon="inline-start" />
                {t(image ? "replace_image_action" : "upload_image_action")}
              </Button>
              <span className="text-xs text-muted-foreground">{t("upload_image_optional")}</span>
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              hidden
              aria-label={t("upload_image_action")}
              onChange={(event) => setImage(event.target.files?.[0] ?? null)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={nameId}>{t("field.name")}</Label>
            <Input
              id={nameId}
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoComplete="off"
              required
              disabled={creating}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={descriptionId}>{t("field.description")}</Label>
            <Textarea
              id={descriptionId}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              disabled={creating}
            />
          </div>
          {type === "character" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={voiceId}>{t("field.voice_style")}</Label>
              <Input
                id={voiceId}
                value={voiceStyle}
                onChange={(event) => setVoiceStyle(event.target.value)}
                disabled={creating}
              />
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={creating} />}>{t("cancel")}</DialogClose>
        <Button type="submit" form={formId} disabled={!trimmedName || creating}>
          {creating && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
          {t("create")}
        </Button>
      </DialogFooter>
    </>
  );
}
