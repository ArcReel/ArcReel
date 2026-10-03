
/**
 * API Keys 管理 Tab — Darkroom redesign
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { AlertTriangle, KeyRound, Loader2, Plus, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { CopyButton } from "@/components/shared/CopyButton";
import { errMsg } from "@/utils/async";
import { formatDate } from "@/utils/date-format";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  ICON_BTN_FILLED_CLS,
  INPUT_CLS,
} from "@/components/shared/darkroom-tokens";
import type { ApiKeyInfo, CreateApiKeyResponse } from "@/types";

const MODAL_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.21 0.012 270 / 0.96), oklch(0.16 0.010 265 / 0.96))",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FULL_DATE_OPTS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
};

function isExpired(expiresAt: string | null): boolean {
  if (!expiresAt) return false;
  return new Date(expiresAt) < new Date();
}

// ---------------------------------------------------------------------------
// Corner brackets — cinematic frame
// ---------------------------------------------------------------------------

function CornerBrackets() {
  const cornerCls =
    "pointer-events-none absolute h-3 w-3 border-primary";
  return (
    <>
      <span aria-hidden className={`${cornerCls} left-2 top-2 border-l border-t`} />
      <span aria-hidden className={`${cornerCls} right-2 top-2 border-r border-t`} />
      <span aria-hidden className={`${cornerCls} left-2 bottom-2 border-l border-b`} />
      <span aria-hidden className={`${cornerCls} right-2 bottom-2 border-r border-b`} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Create Modal
// ---------------------------------------------------------------------------

interface CreateModalProps {
  onClose: () => void;
  onCreated: (key: ApiKeyInfo) => void;
}

function CreateModal({ onClose, onCreated }: CreateModalProps) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [expiresDays, setExpiresDays] = useState<number | "">(30);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CreateApiKeyResponse | null>(null);

  const canCreate = useMemo(() => name.trim().length > 0, [name]);
  const nameInputRef = useAutoFocus<HTMLInputElement>();

  const handleCreate = useCallback(async () => {
    if (!canCreate || creating) return;
    setCreating(true);
    try {
      const days: number | undefined = expiresDays === "" ? 0 : expiresDays;
      const res = await API.createApiKey(name.trim(), days);
      setCreated(res);
      onCreated({
        id: res.id,
        name: res.name,
        key_prefix: res.key_prefix,
        created_at: res.created_at,
        expires_at: res.expires_at,
        last_used_at: null,
      });
    } catch (err) {
      useAppStore.getState().pushToast(t("create_failed", { message: errMsg(err) }), "error");
    } finally {
      setCreating(false);
    }
  }, [canCreate, creating, expiresDays, name, onCreated, t]);

  useEscapeClose(onClose);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !created && canCreate) void handleCreate();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [canCreate, created, handleCreate]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{
        background:
          "radial-gradient(800px 500px at 50% 30%, oklch(0.30 0.04 295 / 0.20), transparent 60%), oklch(0 0 0 / 0.62)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
      }}
    >
      <div
        className="relative w-full max-w-md overflow-hidden rounded-xl border border-border p-6"
        style={MODAL_STYLE}
      >
        <CornerBrackets />
        <div className="mb-5 flex items-center justify-between">
          <div>
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-primary">
              {created ? "Key Issued" : "New Token"}
            </div>
            <h3
              className="font-editorial mt-1"
              style={{
                fontSize: 22,
                fontWeight: 400,
                lineHeight: 1.1,
                letterSpacing: "-0.012em",
                color: "var(--foreground)",
              }}
            >
              {created ? t("key_created") : t("new_api_key")}
            </h3>
          </div>
          {!creating && (
            <button
              type="button"
              onClick={onClose}
              className={ICON_BTN_FILLED_CLS}
              aria-label={t("common:cancel")}
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {!created ? (
          <div className="space-y-5">
            <div>
              <label
                htmlFor="apikey-name"
                className="block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground"
              >
                {t("name")}
              </label>
              <p className="mt-1 text-[12px] leading-[1.55] text-muted-foreground">
                {t("key_name_hint")}
              </p>
              <input
                id="apikey-name"
                ref={nameInputRef}
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("enter_key_name")}
                autoComplete="off"
                className={`mt-2 ${INPUT_CLS}`}
              />
            </div>

            <div>
              <label
                htmlFor="apikey-expires"
                className="block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground"
              >
                {t("expiration_days")}
              </label>
              <p className="mt-1 text-[12px] leading-[1.55] text-muted-foreground">
                {t("zero_permanent_hint")}
              </p>
              <input
                id="apikey-expires"
                type="number"
                min={0}
                value={expiresDays}
                onChange={(e) =>
                  setExpiresDays(e.target.value === "" ? "" : Number(e.target.value))
                }
                className={`mt-2 ${INPUT_CLS} w-1/3`}
              />
            </div>

            <div className="flex justify-end gap-2 border-t border-border/50 pt-4">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md px-3.5 py-2 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("common:cancel")}
              </button>
              <button
                type="button"
                onClick={() => void handleCreate()}
                disabled={!canCreate || creating}
                className={ACCENT_BTN_CLS}
                style={ACCENT_BUTTON_STYLE}
              >
                {creating && (
                  <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
                )}
                {t("common:confirm")}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div
              className="rounded-lg border px-4 py-3 text-[12px] leading-[1.55]"
              style={{
                borderColor: "color-mix(in oklab, var(--warn) 30%, transparent)",
                background: "color-mix(in oklab, var(--warn) 15%, transparent)",
              }}
            >
              <div className="mb-1.5 flex items-center gap-2 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-warn">
                <AlertTriangle className="h-3.5 w-3.5" />
                {t("save_key_warning")}
              </div>
              <p className="text-subtle-foreground">{t("key_not_viewable_again")}</p>
            </div>

            <div className="relative">
              <input
                readOnly
                type="text"
                value={created.key}
                aria-label={t("api_key_label")}
                className="w-full rounded-md border border-border bg-card/65 px-3 py-3 pr-12 font-mono text-[12.5px] tracking-[0.04em] text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <CopyButton
                text={created.key}
                label={t("common:copy")}
                copiedLabel={t("common:copied")}
                className="absolute right-2 top-1/2 -translate-y-1/2"
              />
            </div>

            <div className="flex justify-end pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-border bg-card/55 px-5 py-2 text-[12.5px] text-subtle-foreground transition-colors hover:border-input hover:bg-card hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("common:done")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ApiKeysTab
// ---------------------------------------------------------------------------

export function ApiKeysTab() {
  const { t, i18n } = useTranslation("dashboard");
  const tRef = useRef(t);
  // 同步最新 t 到 ref，供异步回调读取最新翻译函数
  useEffect(() => {
    tRef.current = t;
  }, [t]);
  const [keys, setApiKeys] = useState<ApiKeyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const fetchKeys = useCallback(async () => {
    try {
      const res = await API.listApiKeys();
      setApiKeys(res);
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(tRef.current("load_failed", { message: errMsg(err) }), "error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 时异步拉取 API Key 列表后回写，属于受控的初始化加载
    void fetchKeys();
  }, [fetchKeys]);

  const handleDelete = useCallback(async (key: ApiKeyInfo) => {
    if (!confirm(tRef.current("confirm_delete_key", { name: key.name }))) {
      return;
    }
    setDeletingId(key.id);
    try {
      await API.deleteApiKey(key.id);
      setApiKeys((prev) => prev.filter((k) => k.id !== key.id));
      useAppStore.getState().pushToast(tRef.current("key_deleted_success"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(tRef.current("delete_failed", { message: errMsg(err) }), "error");
    } finally {
      setDeletingId(null);
    }
  }, []);

  return (
    <div className="space-y-6">
      {/* Heading */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-primary">
            Issued Tokens
          </div>
          <h3
            className="font-editorial mt-1 flex items-center gap-2"
            style={{
              fontWeight: 400,
              fontSize: 22,
              lineHeight: 1.1,
              letterSpacing: "-0.012em",
              color: "var(--foreground)",
            }}
          >
            <KeyRound className="h-4 w-4 text-primary" aria-hidden />
            {t("api_key_mgmt")}
          </h3>
          <p className="mt-1.5 text-[12.5px] leading-[1.6] text-muted-foreground">
            {t("api_key_usage_desc")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className={`${ACCENT_BTN_CLS} shrink-0`}
          style={ACCENT_BUTTON_STYLE}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("create_api_key")}
        </button>
      </div>

      {/* Table */}
      <div
        className="overflow-hidden rounded-lg border border-border"
        style={CARD_STYLE}
      >
        <table className="w-full border-collapse text-left text-[12.5px]">
          <thead>
            <tr className="border-b border-border/50">
              {[
                t("name"),
                t("key_prefix"),
                t("created_at"),
                t("expires_at"),
                t("last_used"),
              ].map((label) => (
                <th
                  key={label}
                  className="px-4 py-3 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground"
                >
                  {label}
                </th>
              ))}
              <th className="px-4 py-3 text-right font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
                {t("actions")}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center">
                  <div className="flex items-center justify-center gap-2 text-muted-foreground">
                    <Loader2
                      className="h-3.5 w-3.5 motion-safe:animate-spin text-primary"
                      aria-hidden
                    />
                    <span className="font-mono text-[10.5px] uppercase tracking-[0.14em]">
                      {t("common:loading")}
                    </span>
                  </div>
                </td>
              </tr>
            ) : keys.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center">
                  <div className="mx-auto flex max-w-[240px] flex-col items-center gap-3">
                    <div className="rounded-full border border-border/50 bg-card/45 p-3">
                      <KeyRound className="h-5 w-5 text-muted-foreground" aria-hidden />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-[12.5px] text-muted-foreground">{t("no_api_keys")}</p>
                      <button
                        onClick={() => setShowCreate(true)}
                        className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-primary transition-colors hover:text-primary"
                      >
                        {t("create_one_now")}
                      </button>
                    </div>
                  </div>
                </td>
              </tr>
            ) : (
              keys.map((key) => {
                const expired = isExpired(key.expires_at);
                return (
                  <tr
                    key={key.id}
                    className="group transition-colors hover:bg-card/35"
                  >
                    <td className="px-4 py-4 font-medium text-foreground">{key.name}</td>
                    <td className="px-4 py-4 font-mono text-muted-foreground">
                      {key.key_prefix}****
                    </td>
                    <td className="px-4 py-4 font-mono tabular-nums text-subtle-foreground">
                      {formatDate(key.created_at, i18n.language, FULL_DATE_OPTS)}
                    </td>
                    <td className="px-4 py-4">
                      {!key.expires_at ? (
                        <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
                          {t("permanent")}
                        </span>
                      ) : expired ? (
                        <span
                          className="inline-flex rounded-full px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
                          style={{
                            background: "color-mix(in oklab, var(--warn) 15%, transparent)",
                            color: "var(--warn)",
                            border: "1px solid color-mix(in oklab, var(--warn) 30%, transparent)",
                          }}
                        >
                          {t("expired")}
                        </span>
                      ) : (
                        <span className="font-mono tabular-nums text-subtle-foreground">
                          {formatDate(key.expires_at, i18n.language, FULL_DATE_OPTS)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-4 font-mono tabular-nums text-muted-foreground">
                      {formatDate(key.last_used_at, i18n.language, FULL_DATE_OPTS)}
                    </td>
                    <td className="px-4 py-4 text-right">
                      <button
                        type="button"
                        onClick={() => void handleDelete(key)}
                        disabled={deletingId === key.id}
                        className="rounded-sm p-2 text-muted-foreground transition-colors hover:bg-warn/15 hover:text-warn focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        title={t("common:delete")}
                      >
                        {deletingId === key.id ? (
                          <Loader2
                            className="h-3.5 w-3.5 motion-safe:animate-spin"
                            aria-hidden
                          />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={(k) => setApiKeys((prev) => [k, ...prev])}
        />
      )}
    </div>
  );
}
