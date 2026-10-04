import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { Asset } from "@/types/asset";
import type { ProjectSummary } from "@/types/project";

type ConflictPolicy = "skip" | "overwrite" | "rename";

const CONFLICT_POLICIES: readonly ConflictPolicy[] = ["skip", "rename", "overwrite"];

/** 后端按条目返回的失败原因，未知原因落到通用文案。 */
const FAILURE_KEYS: Record<string, string> = {
  not_found: "apply_failed_not_found",
  image_missing: "apply_failed_image_missing",
  audio_missing: "apply_failed_audio_missing",
  invalid_name: "apply_failed_invalid_name",
  project_name_conflict: "apply_failed_name_conflict",
};

/**
 * 把资产库里的一个资产复制到某个项目。项目里已有同名资产时，按所选方式跳过、另起新名或覆盖。
 */
export function ApplyToProjectDialog({ asset, onClose }: { asset: Asset | null; onClose: () => void }) {
  const [applying, setApplying] = useState(false);
  const [shown, setShown] = useState(asset);
  if (asset !== null && asset !== shown) setShown(asset);

  return (
    <Dialog
      open={asset !== null}
      onOpenChange={(next) => {
        // 请求在途时不响应 Esc 与遮罩点击，避免结果还没回来对话框先消失
        if (!next && !applying) onClose();
      }}
    >
      <DialogContent showCloseButton={!applying}>
        {shown && <ApplyToProjectForm asset={shown} applying={applying} setApplying={setApplying} onDone={onClose} />}
      </DialogContent>
    </Dialog>
  );
}

function ApplyToProjectForm({
  asset,
  applying,
  setApplying,
  onDone,
}: {
  asset: Asset;
  applying: boolean;
  setApplying: (applying: boolean) => void;
  onDone: () => void;
}) {
  const { t } = useTranslation("assets");
  const projectLabelId = useId();
  const policyLabelId = useId();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [policy, setPolicy] = useState<ConflictPolicy>("skip");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.listProjects({ signal: controller.signal }).then(
      ({ projects: list }) => {
        if (!controller.signal.aborted) setProjects(list);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setLoadError(errMsg(err));
      },
    );
    return () => controller.abort();
  }, []);

  const items = (projects ?? []).map((project) => ({ value: project.name, label: project.title || project.name }));
  const targetLabel = items.find((item) => item.value === target)?.label ?? "";

  const handleApply = async () => {
    if (!target || applying) return;
    setApplying(true);
    setError(null);
    try {
      const result = await API.applyAssetsToProject({
        asset_ids: [asset.id],
        target_project: target,
        conflict_policy: policy,
      });
      const failure = result.failed[0];
      if (failure) {
        setError(t(FAILURE_KEYS[failure.reason] ?? "apply_failed_generic"));
        return;
      }
      const { pushToast } = useAppStore.getState();
      if (result.skipped.length > 0) {
        pushToast(t("apply_skipped", { name: asset.name, project: targetLabel }), "info");
      } else {
        pushToast(t("apply_success", { name: result.succeeded[0]?.name ?? asset.name, project: targetLabel }), "success");
      }
      onDone();
    } catch (err) {
      setError(t("apply_failed", { message: errMsg(err) }));
    } finally {
      setApplying(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("apply_title", { name: asset.name })}</DialogTitle>
        <DialogDescription>{t("apply_description")}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <span id={projectLabelId} className="text-sm font-medium">
              {t("apply_target_label")}
            </span>
            {loadError ? (
              <p role="alert" className="text-sm text-destructive">
                {t("apply_projects_failed", { message: loadError })}
              </p>
            ) : projects !== null && projects.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("apply_no_projects")}</p>
            ) : (
              <Select items={items} value={target} onValueChange={(value: string | null) => setTarget(value)}>
                <SelectTrigger aria-labelledby={projectLabelId} disabled={projects === null || applying} className="w-full">
                  <SelectValue placeholder={projects === null ? t("loading") : t("apply_target_placeholder")} />
                </SelectTrigger>
                <SelectContent>
                  {items.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <span id={policyLabelId} className="text-sm font-medium">
              {t("apply_conflict_label")}
            </span>
            <RadioGroup
              aria-labelledby={policyLabelId}
              value={policy}
              onValueChange={(value) => setPolicy(value as ConflictPolicy)}
              disabled={applying}
            >
              {CONFLICT_POLICIES.map((value) => (
                <Label key={value}>
                  <RadioGroupItem value={value} />
                  {t(`apply_conflict_${value}`)}
                </Label>
              ))}
            </RadioGroup>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={applying} />}>{t("cancel")}</DialogClose>
        <Button disabled={!target || applying} onClick={() => void handleApply()}>
          {applying && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
          {t("apply_confirm")}
        </Button>
      </DialogFooter>
    </>
  );
}
