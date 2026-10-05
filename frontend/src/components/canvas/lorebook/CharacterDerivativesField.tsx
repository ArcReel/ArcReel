import { useCallback, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { CharacterDerivative } from "@/types";
import { AssetImageDialog } from "./AssetImageDialog";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { CharacterDerivativeRow } from "./CharacterDerivativeRow";
import { useTrackWrite } from "./useAssetWrites";
import { useCharacterDerivativeSheets } from "./useCharacterDerivativeSheets";

interface CharacterDerivativesFieldProps {
  projectName: string;
  characterName: string;
  derivatives: Record<string, CharacterDerivative>;
  /** 本体资产图路径；没有时衍生不能生成。 */
  ownerSheet?: string;
  readOnly: boolean;
  /** 详情里其他写入或本体生成占用中。 */
  busy: boolean;
}

/**
 * 角色详情的「衍生」区块：逐条列出衍生，区块头有「新增衍生」。新增、改名、删除立即执行；
 * 每条的外观变化是独立的编辑单元，在所在行保存。写请求经详情编辑器的写入登记计入占用态。
 */
export function CharacterDerivativesField({
  projectName,
  characterName,
  derivatives,
  ownerSheet,
  readOnly,
  busy,
}: CharacterDerivativesFieldProps) {
  const { t } = useTranslation(["assets", "common"]);
  const ownerFp = useProjectsStore((s) => (ownerSheet ? s.getAssetFingerprint(ownerSheet) : null));
  // 登记或本体图变化时重新读取衍生图状态（过期判定依赖二者）
  const revision = `${JSON.stringify(derivatives)}#${ownerSheet ?? ""}#${ownerFp ?? ""}`;
  const entries = Object.entries(derivatives);
  const { statuses, refresh } = useCharacterDerivativeSheets(projectName, characterName, revision, entries.length > 0);
  // 改名后新名称沿用原来那一行，行内未保存的外观变化不丢
  const [rowKeys, setRowKeys] = useState<Record<string, string>>({});
  // 关闭动画期间标题沿用上一张图
  const [viewing, setViewing] = useState<{ src: string; alt: string; title: string; open: boolean } | null>(null);

  const handleRenamed = useCallback((from: string, to: string) => {
    setRowKeys((prev) => ({ ...prev, [to]: prev[from] ?? from }));
  }, []);

  if (readOnly && entries.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("assets:derivative_hint")}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {entries.map(([name, derivative]) => (
            <CharacterDerivativeRow
              key={rowKeys[name] ?? name}
              projectName={projectName}
              characterName={characterName}
              name={name}
              derivative={derivative}
              status={statuses[name]}
              ownerHasSheet={Boolean(ownerSheet)}
              readOnly={readOnly}
              busy={busy}
              onView={(image) => setViewing({ ...image, open: true })}
              onRenamed={handleRenamed}
              onSheetsChanged={refresh}
            />
          ))}
        </ul>
      )}
      {readOnly ? null : (
        <AddDerivative projectName={projectName} characterName={characterName} busy={busy} onAdded={refresh} />
      )}
      <AssetImageDialog
        src={viewing?.open ? viewing.src : null}
        title={viewing?.title ?? ""}
        alt={viewing?.alt ?? ""}
        onClose={() => setViewing((prev) => (prev ? { ...prev, open: false } : prev))}
      />
    </div>
  );
}

function AddDerivative({
  projectName,
  characterName,
  busy,
  onAdded,
}: {
  projectName: string;
  characterName: string;
  busy: boolean;
  onAdded: () => void;
}) {
  const { t } = useTranslation(["assets", "common"]);
  const nameId = useId();
  const descriptionId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const track = useTrackWrite();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setDescription("");
    setError(null);
  };

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || submitting) return;
    if (busy) {
      setError(t("assets:derivative_busy_hint"));
      return;
    }
    if (rejectIfAssetBusy("character", projectName, characterName, t, "assets:derivative_busy_hint")) return;
    setSubmitting(true);
    setError(null);
    try {
      await track(
        API.addCharacterDerivative(projectName, characterName, trimmed, description.trim()).then(() =>
          useProjectsStore.getState().refreshProject(projectName),
        ),
      );
      onAdded();
      reset();
      setOpen(false);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        // 提交中不响应关闭，请求在途时输入不丢
        if (!next && submitting) return;
        setOpen(next);
        if (!next) reset();
      }}
    >
      <PopoverTrigger render={<Button variant="outline" size="sm" className="self-start" disabled={busy} />}>
        <Plus aria-hidden data-icon="inline-start" />
        {t("assets:derivative_add")}
      </PopoverTrigger>
      <PopoverContent align="start" initialFocus={nameRef} className="w-80">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={nameId}>{t("assets:derivative_name")}</Label>
            <Input
              ref={nameRef}
              id={nameId}
              value={name}
              disabled={submitting}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("assets:derivative_name_placeholder")}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={descriptionId}>{t("assets:derivative_description")}</Label>
            <Textarea
              id={descriptionId}
              value={description}
              disabled={submitting}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("assets:derivative_description_placeholder")}
              rows={2}
            />
          </div>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" size="sm" className="self-end" disabled={submitting || name.trim().length === 0}>
            {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {t("common:add")}
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
