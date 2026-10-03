/**
 * 社交分发设置：Upload-Post 凭证 + 已连接账号一览。
 *
 * 凭证与账号分两步呈现：凭证没存住就列不出账号，把两者塞进同一次请求会让「密钥错了」
 * 和「这个档案一个号都没连」退化成同一条报错。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import { API } from "@/api";
import type { SystemConfigPatch, SystemConfigSettings } from "@/types/system";
import type { SocialPublishProfile } from "@/types/social-publish";
import { InlineWarning } from "@/components/ui/InlineWarning";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE } from "@/components/ui/darkroom-tokens";

const INPUT_CLS =
  "w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
const LABEL_CLS =
  "mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4";

interface CardProps {
  kicker: string;
  title: string;
  description?: string;
  children: React.ReactNode;
}

function SectionCard({ kicker, title, description, children }: CardProps) {
  return (
    <div className="rounded-[10px] border border-hairline p-5" style={CARD_STYLE}>
      <div className="mb-4">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {kicker}
        </div>
        <h4 className="mt-1.5 text-[14px] font-medium text-text">{title}</h4>
        {description && (
          <p className="mt-1 text-[12px] leading-[1.55] text-text-3">{description}</p>
        )}
      </div>
      {children}
    </div>
  );
}

export function SocialPublishSection() {
  const { t } = useTranslation(["dashboard", "common"]);

  const [settings, setSettings] = useState<SystemConfigSettings | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [draft, setDraft] = useState<SystemConfigPatch>({});
  const [saving, setSaving] = useState(false);
  const [profiles, setProfiles] = useState<SocialPublishProfile[] | null>(null);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [loadingProfiles, setLoadingProfiles] = useState(false);

  /** 回读是否成功。读配置失败要说出来：静默吞掉 rejection 会让 settings 一直是 null，
   *  界面卡在转圈图标上，除非整块重新挂载。 */
  const fetchConfig = useCallback(async () => {
    setConfigError(null);
    try {
      const res = await API.getSystemConfig();
      setSettings(res.settings);
      setDraft({});
      return true;
    } catch (err) {
      setConfigError(errMsg(err));
      return false;
    }
  }, []);

  useEffect(() => {
    // mount 时异步拉取配置，回调内 setSettings（异步 fetch 后回写）
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchConfig();
  }, [fetchConfig]);

  const loadProfiles = useCallback(async () => {
    setLoadingProfiles(true);
    setProfilesError(null);
    try {
      const res = await API.getSocialPublishProfiles();
      setProfiles(res.profiles);
    } catch (err) {
      setProfiles(null);
      setProfilesError(errMsg(err));
    } finally {
      setLoadingProfiles(false);
    }
  }, []);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      await API.updateSystemConfig(draft);
      // PATCH 成功但回读失败时不报成功：旧的 settings 还在，界面看起来像是已经刷新，
      // 而用户看到的其实是保存前的那一份。
      const reloaded = await fetchConfig();
      if (!reloaded) return;
      useAppStore.getState().pushToast(t("dashboard:social_publish_saved"), "success");
      // 凭证刚换过，旧的账号列表已经无效：重取而不是留着上一份显示。
      void loadProfiles();
    } catch (err) {
      useAppStore.getState().pushToast(t("dashboard:save_failed", { message: errMsg(err) }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, fetchConfig, loadProfiles, t]);

  if (!settings) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-10 text-text-4">
        {configError === null ? (
          <Loader2 className="h-4 w-4 motion-safe:animate-spin" aria-hidden />
        ) : (
          <>
            <InlineWarning message={configError} />
            <button
              type="button"
              onClick={() => void fetchConfig()}
              className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {t("common:retry")}
            </button>
          </>
        )}
      </div>
    );
  }

  const keyIsSet = settings.upload_post_api_key?.is_set ?? false;
  const currentProfile = draft.upload_post_profile ?? settings.upload_post_profile ?? "";
  const currentBaseUrl = draft.upload_post_base_url ?? settings.upload_post_base_url ?? "";
  const isDirty = Object.keys(draft).length > 0;

  return (
    <div className="space-y-4 p-6">
      {/* 首次加载失败走上面的空态分支；这里是「已经有一份配置、但刚才那次回读失败了」，
          不画出来的话保存后界面看着像已刷新，其实显示的是保存前的那一份。 */}
      {configError !== null && <InlineWarning message={configError} />}
      <SectionCard
        kicker="Distribution"
        title={t("dashboard:social_publish_credentials_title")}
        description={t("dashboard:social_publish_credentials_desc")}
      >
        <div className="space-y-4">
          <div>
            <label htmlFor="upload-post-api-key" className={LABEL_CLS}>
              {t("dashboard:social_publish_api_key_label")}
            </label>
            <input
              id="upload-post-api-key"
              type="password"
              autoComplete="off"
              value={draft.upload_post_api_key ?? ""}
              placeholder={
                keyIsSet
                  ? t("dashboard:social_publish_api_key_configured")
                  : t("dashboard:social_publish_api_key_placeholder")
              }
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, upload_post_api_key: event.target.value }))
              }
              className={`${INPUT_CLS} font-mono`}
            />
            <p className="mt-1 text-[11px] text-text-4">
              {keyIsSet
                ? t("dashboard:social_publish_api_key_set_hint")
                : t("dashboard:social_publish_api_key_hint")}
            </p>
          </div>

          <div>
            <label htmlFor="upload-post-profile" className={LABEL_CLS}>
              {t("dashboard:social_publish_profile_label")}
            </label>
            <input
              id="upload-post-profile"
              type="text"
              value={currentProfile}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, upload_post_profile: event.target.value }))
              }
              className={INPUT_CLS}
            />
            <p className="mt-1 text-[11px] text-text-4">{t("dashboard:social_publish_profile_hint")}</p>
          </div>

          <div>
            <label htmlFor="upload-post-base-url" className={LABEL_CLS}>
              {t("dashboard:social_publish_base_url_label")}
            </label>
            <input
              id="upload-post-base-url"
              type="url"
              value={currentBaseUrl}
              placeholder="https://api.upload-post.com/api"
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, upload_post_base_url: event.target.value }))
              }
              className={`${INPUT_CLS} font-mono`}
            />
            <p className="mt-1 text-[11px] text-text-4">{t("dashboard:social_publish_base_url_hint")}</p>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        kicker="Connected Accounts"
        title={t("dashboard:social_publish_accounts_title")}
        description={t("dashboard:social_publish_accounts_desc")}
      >
        <button
          type="button"
          onClick={() => void loadProfiles()}
          disabled={loadingProfiles}
          className="mb-3 inline-flex items-center gap-1.5 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline-strong hover:text-text disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {loadingProfiles ? (
            <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          )}
          {t("dashboard:social_publish_accounts_refresh")}
        </button>

        {profilesError && <p className="text-[12px] text-warm">{profilesError}</p>}

        {profiles !== null && profiles.length === 0 && (
          <p className="text-[12px] text-text-3">{t("dashboard:social_publish_accounts_empty")}</p>
        )}

        {profiles?.map((profile) => (
          <div key={profile.username} className="mb-3 last:mb-0">
            <div className="font-mono text-[11px] text-text-3">{profile.username}</div>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {profile.accounts.map((account) => (
                <li
                  key={account.platform}
                  className="rounded-[6px] border border-hairline px-2 py-1 text-[11.5px] text-text-2"
                >
                  <span className="text-text">{account.platform}</span>
                  {account.handle && <span className="ml-1 text-text-4">{account.handle}</span>}
                  {account.reauth_required && (
                    <span className="ml-1.5 text-warm">
                      {t("dashboard:social_publish_reauth_required")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </SectionCard>

      {isDirty && (
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden /> : null}
            {saving ? t("common:saving") : t("common:save")}
          </button>
          <button
            type="button"
            onClick={() => setDraft({})}
            className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {t("common:reset")}
          </button>
        </div>
      )}
    </div>
  );
}
