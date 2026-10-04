import { useId, useState, type FormEvent } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiKeyInfo, CreateApiKeyResponse } from "@/types";
import { errMsg } from "@/utils/async";

import { CopyableValue } from "./CopyableValue";

const DEFAULT_EXPIRES_DAYS = "30";

interface CreateAccessTokenDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 创建成功后立即回调（完整令牌仍在对话框里展示），调用方据此更新列表。 */
  onCreated?: (token: ApiKeyInfo) => void;
}

/** 创建访问令牌：填名称与有效期，创建成功后在同一对话框里一次性展示完整令牌。「外部 Agent 接入」与「访问令牌」共用。 */
export function CreateAccessTokenDialog({ open, onOpenChange, onCreated }: CreateAccessTokenDialogProps) {
  const [creating, setCreating] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // 请求在途时不响应 Esc 与遮罩点击，避免令牌已签发却没能展示
        if (!next && creating) return;
        onOpenChange(next);
      }}
    >
      <DialogContent showCloseButton={!creating}>
        {/* 每次打开都从空表单开始：内容只在打开期间挂载 */}
        <CreateAccessTokenForm creating={creating} setCreating={setCreating} onCreated={onCreated} />
      </DialogContent>
    </Dialog>
  );
}

function CreateAccessTokenForm({
  creating,
  setCreating,
  onCreated,
}: {
  creating: boolean;
  setCreating: (creating: boolean) => void;
  onCreated?: (token: ApiKeyInfo) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const nameId = useId();
  const nameHintId = useId();
  const expiresId = useId();
  const expiresHintId = useId();
  const [name, setName] = useState("");
  const [expiresDays, setExpiresDays] = useState(DEFAULT_EXPIRES_DAYS);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreateApiKeyResponse | null>(null);

  const trimmedName = name.trim();
  const days = Number(expiresDays);
  const daysValid = expiresDays.trim() !== "" && Number.isInteger(days) && days >= 0;
  const canSubmit = trimmedName.length > 0 && daysValid && !creating;

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setCreating(true);
    setError(null);
    try {
      const res = await API.createApiKey(trimmedName, days);
      setCreated(res);
      onCreated?.({
        id: res.id,
        name: res.name,
        key_prefix: res.key_prefix,
        created_at: res.created_at,
        expires_at: res.expires_at,
        last_used_at: null,
      });
    } catch (err) {
      setError(t("dashboard:access_token_create_failed", { message: errMsg(err) }));
    } finally {
      setCreating(false);
    }
  };

  if (created) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>{t("dashboard:access_token_created_title")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/10 p-3">
              <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
              {/* 琥珀浅底上 muted-foreground 达不到 4.5:1，提示文字用中间档 */}
              <p className="text-sm text-subtle-foreground">{t("dashboard:access_token_created_warning")}</p>
            </div>
            <CopyableValue
              label={t("dashboard:access_token_value")}
              value={created.key}
              copyLabel={t("dashboard:access_token_copy")}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button />}>{t("common:done")}</DialogClose>
        </DialogFooter>
      </>
    );
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("dashboard:access_token_create")}</DialogTitle>
      </DialogHeader>
      <DialogBody>
        <form id={formId} onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor={nameId}>{t("dashboard:access_token_name")}</Label>
            <Input
              id={nameId}
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-describedby={nameHintId}
              autoComplete="off"
              disabled={creating}
            />
            <p id={nameHintId} className="text-xs text-muted-foreground">
              {t("dashboard:access_token_name_hint")}
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={expiresId}>{t("dashboard:access_token_expires_days")}</Label>
            <Input
              id={expiresId}
              type="number"
              inputMode="numeric"
              min={0}
              step={1}
              value={expiresDays}
              onChange={(e) => setExpiresDays(e.target.value)}
              aria-describedby={expiresHintId}
              aria-invalid={!daysValid || undefined}
              disabled={creating}
              className="w-32"
            />
            <p id={expiresHintId} className="text-xs text-muted-foreground">
              {t("dashboard:access_token_expires_hint")}
            </p>
          </div>
          {error && (
            <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" />} disabled={creating}>
          {t("common:cancel")}
        </DialogClose>
        <Button type="submit" form={formId} disabled={!canSubmit}>
          {creating && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
          {creating ? t("dashboard:access_token_creating") : t("dashboard:access_token_create_submit")}
        </Button>
      </DialogFooter>
    </>
  );
}
