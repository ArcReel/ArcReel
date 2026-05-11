import { CheckCircle, Edit2, Loader2, PlayCircle, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentCredential } from "@/types/agent-credential";
import { CARD_STYLE, GHOST_BTN_CLS, ICON_BTN_CLS } from "@/components/ui/darkroom-tokens";

import { PresetIcon } from "./PresetIcon";

interface Props {
  credentials: AgentCredential[];
  busyId?: number | null;
  onActivate: (id: number) => void;
  onTest: (id: number) => void;
  onEdit: (cred: AgentCredential) => void;
  onDelete: (id: number) => void;
}

export function CredentialList({
  credentials,
  busyId = null,
  onActivate,
  onTest,
  onEdit,
  onDelete,
}: Props) {
  const { t } = useTranslation("dashboard");

  if (credentials.length === 0) {
    return (
      <div
        data-testid="credential-list-empty"
        className="rounded-[10px] border border-dashed border-hairline px-4 py-8 text-center text-[12.5px] text-text-3"
      >
        {t("cred_list_empty")}
      </div>
    );
  }

  return (
    <ul className="grid gap-2.5">
      {credentials.map((c) => (
        <li
          key={c.id}
          className={`relative flex items-center gap-3 rounded-[10px] border px-3 py-3 ${
            c.is_active
              ? "border-accent/40 before:absolute before:bottom-2 before:left-0 before:top-2 before:w-[2px] before:rounded-r before:bg-accent"
              : "border-hairline"
          }`}
          style={CARD_STYLE}
        >
          <PresetIcon iconKey={c.icon_key} size={28} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-[13px] font-medium text-text">
                {c.display_name}
              </span>
              {c.is_active && (
                <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.14em] text-accent">
                  <CheckCircle className="h-2.5 w-2.5" aria-hidden />
                  {t("is_active")}
                </span>
              )}
            </div>
            <div className="mt-0.5 truncate font-mono text-[10.5px] text-text-4">
              {c.base_url} · {c.api_key_masked}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onTest(c.id)}
              disabled={busyId === c.id}
              className={GHOST_BTN_CLS}
            >
              {busyId === c.id ? (
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
              ) : (
                <PlayCircle className="h-3.5 w-3.5" aria-hidden />
              )}
              {t("test_credential")}
            </button>
            {!c.is_active && (
              <button
                type="button"
                onClick={() => onActivate(c.id)}
                disabled={busyId === c.id}
                className={GHOST_BTN_CLS}
              >
                {t("cred_activate_label")}
              </button>
            )}
            <button
              type="button"
              onClick={() => onEdit(c)}
              className={ICON_BTN_CLS}
              aria-label={t("cred_edit_label")}
            >
              <Edit2 className="h-3.5 w-3.5" aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => onDelete(c.id)}
              disabled={c.is_active || busyId === c.id}
              className={ICON_BTN_CLS}
              aria-label={t("cred_delete_label")}
              title={c.is_active ? t("cred_delete_active_blocked") : undefined}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
