import { useCallback, useId, useRef, useState } from "react";
import { Loader2, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useConfirmLeave, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { TruncatedText } from "@/components/shared/TruncatedText";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { AssetLibraryAccount, AssetLibraryAccountList, AssetLibrarySite } from "@/types";
import { errMsg } from "@/utils/async";

import { siteLabelKey } from "./asset-library-labels";

const LABEL_CLS = "text-sm font-medium text-foreground";

type DialogState = { mode: "add" } | { mode: "edit"; account: AssetLibraryAccount } | null;

/** 「素材库账号」块：列出控制面账号，增改在对话框里提交、删除经 AlertDialog 确认，都立即生效。 */
export function AssetLibraryAccountsBlock({
  data,
  onChanged,
}: {
  data: AssetLibraryAccountList;
  /** 账号增删改之后调用，上层据此重新读取账号与绑定。 */
  onChanged: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const headingId = useId();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<AssetLibraryAccount | null>(null);
  const closeDialog = useCallback(() => setDialog(null), []);
  const handleDialogDone = useCallback(() => {
    setDialog(null);
    onChanged();
  }, [onChanged]);

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex items-start gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 id={headingId} className="text-base font-medium">
            {t("asset_library_accounts_title")}
          </h3>
          <p className="text-sm text-muted-foreground">{t("asset_library_accounts_desc")}</p>
        </div>
        <Button variant="outline" size="sm" className="shrink-0" onClick={() => setDialog({ mode: "add" })}>
          <Plus data-icon="inline-start" />
          {t("asset_library_account_add")}
        </Button>
      </div>

      {data.accounts.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {t("asset_library_accounts_empty")}
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {data.accounts.map((account) => (
            <li key={account.id} className="flex items-center gap-2 px-3 py-2.5">
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <TruncatedText text={account.name} className="text-sm font-medium" />
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                  <span>{t(siteLabelKey(account.site))}</span>
                  <span className="font-mono">{account.region}</span>
                  <span>{t("asset_library_project_value", { project: account.project_name })}</span>
                  <span className="font-mono">{account.access_key_id_masked}</span>
                </div>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t("asset_library_account_more_actions", { name: account.name })}
                    />
                  }
                >
                  <MoreHorizontal />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setDialog({ mode: "edit", account })}>
                    <Pencil />
                    {t("common:edit")}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onClick={() => setDeleting(account)}>
                    <Trash2 />
                    {t("common:delete")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ))}
        </ul>
      )}

      {dialog && (
        <AccountDialog
          key={dialog.mode === "edit" ? dialog.account.id : "add"}
          account={dialog.mode === "edit" ? dialog.account : null}
          data={data}
          onCancel={closeDialog}
          onDone={handleDialogDone}
        />
      )}
      <DeleteAccountDialog
        account={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={() => {
          setDeleting(null);
          onChanged();
        }}
      />
    </section>
  );
}

type AccountDraft = {
  site: AssetLibrarySite;
  name: string;
  project: string;
  accessKeyId: string;
  secretAccessKey: string;
};

/** 新建时提交完整字段；编辑时留空的密钥沿用已保存的值，站点未变则不提交。 */
function saveAccount(account: AssetLibraryAccount | null, draft: AccountDraft) {
  const common = { name: draft.name.trim(), project_name: draft.project.trim() };
  if (account === null) {
    return API.createAssetLibraryAccount({
      ...common,
      site: draft.site,
      access_key_id: draft.accessKeyId.trim(),
      secret_access_key: draft.secretAccessKey.trim(),
    });
  }
  return API.updateAssetLibraryAccount(account.id, {
    ...common,
    site: draft.site !== account.site ? draft.site : undefined,
    access_key_id: draft.accessKeyId.trim() || undefined,
    secret_access_key: draft.secretAccessKey.trim() || undefined,
  });
}

/** 密钥输入框；编辑已有账号时占位符给出已保存值的掩码，留空表示沿用。 */
function SecretField({
  id,
  label,
  value,
  onChange,
  keptMasked,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  keptMasked: string | undefined;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className={LABEL_CLS}>
        {label}
      </label>
      <Input
        mono
        id={id}
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={keptMasked ? t("credential_keep_existing", { masked: keptMasked }) : undefined}
      />
    </div>
  );
}

function AccountDialog({
  account,
  data,
  onCancel,
  onDone,
}: {
  account: AssetLibraryAccount | null;
  data: AssetLibraryAccountList;
  onCancel: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const idPrefix = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  // 打开时的取值，判断是否有未保存的填写；之后 data 刷新不改变它
  const [initial] = useState(() => ({
    site: account?.site ?? data.sites[0]?.id ?? "volcengine",
    name: account?.name ?? "",
    project: account?.project_name ?? data.default_project_name,
  }));
  const [site, setSite] = useState<AssetLibrarySite>(initial.site);
  const [name, setName] = useState(initial.name);
  const [project, setProject] = useState(initial.project);
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const creating = account === null;
  const complete = name.trim() && project.trim() && (!creating || (accessKeyId.trim() && secretAccessKey.trim()));
  const siteItems = data.sites.map((s) => ({ value: s.id, label: t(siteLabelKey(s.id)) }));
  const region = data.sites.find((s) => s.id === site)?.regions[0] ?? "";

  const dirty =
    site !== initial.site ||
    name !== initial.name ||
    project !== initial.project ||
    accessKeyId !== "" ||
    secretAccessKey !== "";

  const handleSubmit = useCallback(async (): Promise<boolean> => {
    if (!complete) return false;
    setSubmitting(true);
    setError(null);
    try {
      await saveAccount(account, { site, name, project, accessKeyId, secretAccessKey });
      onDone();
      return true;
    } catch (err) {
      setError(errMsg(err));
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [complete, account, site, name, project, accessKeyId, secretAccessKey, onDone]);

  // 未保存的填写登记到离开拦截；关闭对话框也经它询问，不静默丢弃
  useLeaveGuard({ dirty, saving: submitting, save: handleSubmit, discard: onCancel });
  const confirmLeave = useConfirmLeave();
  const requestClose = () => {
    if (submitting) return;
    if (dirty) confirmLeave(onCancel);
    else onCancel();
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) requestClose();
      }}
    >
      <DialogContent initialFocus={nameRef}>
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(e) => {
            e.preventDefault();
            void handleSubmit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{creating ? t("asset_library_account_add") : t("asset_library_account_edit_title")}</DialogTitle>
            <DialogDescription>{t("asset_library_account_dialog_desc")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <label htmlFor={`${idPrefix}-name`} className={LABEL_CLS}>
                  {t("asset_library_account_name")}
                </label>
                <Input
                  id={`${idPrefix}-name`}
                  ref={nameRef}
                  autoComplete="off"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <span id={`${idPrefix}-site`} className={LABEL_CLS}>
                  {t("asset_library_site")}
                </span>
                <Select
                  items={siteItems}
                  value={site}
                  onValueChange={(next) => {
                    if (next !== null) setSite(next);
                  }}
                >
                  <SelectTrigger aria-labelledby={`${idPrefix}-site`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {siteItems.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">{t("asset_library_region_hint", { region })}</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor={`${idPrefix}-project`} className={LABEL_CLS}>
                  {t("asset_library_project")}
                </label>
                <Input
                  mono
                  id={`${idPrefix}-project`}
                  autoComplete="off"
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">{t("asset_library_project_hint")}</p>
              </div>
              <SecretField
                id={`${idPrefix}-ak`}
                label={t("object_storage_access_key_id")}
                value={accessKeyId}
                onChange={setAccessKeyId}
                keptMasked={account?.access_key_id_masked}
              />
              <SecretField
                id={`${idPrefix}-sk`}
                label={t("object_storage_secret_access_key")}
                value={secretAccessKey}
                onChange={setSecretAccessKey}
                keptMasked={account?.secret_access_key_masked}
              />
              {error && (
                <p role="alert" className="text-sm wrap-break-word text-destructive">
                  {error}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={requestClose}>
              {t("common:cancel")}
            </Button>
            <Button type="submit" disabled={submitting || !complete}>
              {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
              {creating ? t("asset_library_account_add") : t("common:save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteAccountDialog({
  account,
  onClose,
  onDeleted,
}: {
  account: AssetLibraryAccount | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 关闭动画期间 account 已清空，沿用最后一次的内容，标题不闪成空白
  const [shown, setShown] = useState(account);
  if (account && account !== shown) {
    setShown(account);
    setError(null);
  }

  const handleDelete = async () => {
    if (!account) return;
    setSubmitting(true);
    setError(null);
    try {
      await API.deleteAssetLibraryAccount(account.id);
      onDeleted();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open={account !== null}
      onOpenChange={(open) => {
        if (!open && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("asset_library_account_delete_title", { name: shown?.name ?? "" })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-2">
            <AlertDialogDescription>{t("asset_library_account_delete_desc")}</AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm wrap-break-word text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void handleDelete()}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("common:delete")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
