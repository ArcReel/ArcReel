/**
 * 社交分发设置：Upload-Post 凭证 + 已连接账号一览。
 *
 * 凭证与账号分两步呈现：凭证没存住就列不出账号，把两者塞进同一次请求会让「密钥错了」
 * 和「这个档案一个号都没连」退化成同一条报错。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InlineWarning } from "@/components/shared/InlineWarning";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { PageShellFooter } from "@/components/shared/page-shell/PageShell";
import type { SystemConfigPatch, SystemConfigSettings } from "@/types/system";
import type { SocialPublishProfile } from "@/types/social-publish";
import { errMsg } from "@/utils/async";

interface SocialPublishFields {
  /** 新密钥；已存的密钥从不回显，空串表示保持不变。 */
  upload_post_api_key: string;
  upload_post_profile: string;
  upload_post_base_url: string;
}

function fieldsFrom(settings: SystemConfigSettings | null): SocialPublishFields {
  return {
    upload_post_api_key: "",
    upload_post_profile: settings?.upload_post_profile ?? "",
    upload_post_base_url: settings?.upload_post_base_url ?? "",
  };
}

function changedFields(fields: SocialPublishFields, saved: SocialPublishFields): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (fields.upload_post_api_key !== "") patch.upload_post_api_key = fields.upload_post_api_key;
  if (fields.upload_post_profile !== saved.upload_post_profile) patch.upload_post_profile = fields.upload_post_profile;
  if (fields.upload_post_base_url !== saved.upload_post_base_url) {
    patch.upload_post_base_url = fields.upload_post_base_url;
  }
  return patch;
}

function SettingsCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">{title}</h3>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function FieldHint({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <p id={id} className="text-xs text-muted-foreground">
      {children}
    </p>
  );
}

export function SocialPublishSection() {
  const { t } = useTranslation(["dashboard", "common"]);

  const [settings, setSettings] = useState<SystemConfigSettings | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<SocialPublishProfile[] | null>(null);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [loadingProfiles, setLoadingProfiles] = useState(false);

  // 读配置失败要说出来：静默吞掉 rejection 会让 settings 一直是 null，界面卡在加载态上。
  const fetchConfig = useCallback(async () => {
    setConfigError(null);
    try {
      const res = await API.getSystemConfig();
      setSettings(res.settings);
    } catch (err) {
      setConfigError(errMsg(err));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 时异步拉取配置后回写
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

  const source = useMemo(() => fieldsFrom(settings), [settings]);
  const saveFields = useCallback(
    async (fields: SocialPublishFields, saved: SocialPublishFields) => {
      const res = await API.updateSystemConfig(changedFields(fields, saved));
      setSettings(res.settings);
      // 凭证刚换过，旧的账号列表已经无效：重取而不是留着上一份显示。
      void loadProfiles();
      return fieldsFrom(res.settings);
    },
    [loadProfiles],
  );
  const unit = useEditUnit({ source, save: saveFields });
  const fields = unit.value;
  const setFields = unit.setValue;

  if (!settings) {
    return configError === null ? (
      <div className="flex items-center gap-2 py-12 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        {t("common:loading")}
      </div>
    ) : (
      <div className="flex flex-col items-start gap-3 py-12">
        <InlineWarning message={configError} />
        <Button variant="outline" onClick={() => void fetchConfig()}>
          {t("common:retry")}
        </Button>
      </div>
    );
  }

  const keyIsSet = settings.upload_post_api_key?.is_set ?? false;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-medium">{t("dashboard:social_publish_section_title")}</h2>
      </header>

      <SettingsCard
        title={t("dashboard:social_publish_credentials_title")}
        description={t("dashboard:social_publish_credentials_desc")}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="upload-post-api-key">{t("dashboard:social_publish_api_key_label")}</Label>
          <Input
            id="upload-post-api-key"
            type="password"
            autoComplete="off"
            aria-describedby="upload-post-api-key-hint"
            value={fields.upload_post_api_key}
            placeholder={
              keyIsSet
                ? t("dashboard:social_publish_api_key_configured")
                : t("dashboard:social_publish_api_key_placeholder")
            }
            onChange={(event) => setFields((prev) => ({ ...prev, upload_post_api_key: event.target.value }))}
          />
          <FieldHint id="upload-post-api-key-hint">
            {keyIsSet ? t("dashboard:social_publish_api_key_set_hint") : t("dashboard:social_publish_api_key_hint")}
          </FieldHint>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="upload-post-profile">{t("dashboard:social_publish_profile_label")}</Label>
          <Input
            id="upload-post-profile"
            type="text"
            aria-describedby="upload-post-profile-hint"
            value={fields.upload_post_profile}
            onChange={(event) => setFields((prev) => ({ ...prev, upload_post_profile: event.target.value }))}
          />
          <FieldHint id="upload-post-profile-hint">{t("dashboard:social_publish_profile_hint")}</FieldHint>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="upload-post-base-url">{t("dashboard:social_publish_base_url_label")}</Label>
          <Input
            id="upload-post-base-url"
            type="url"
            aria-describedby="upload-post-base-url-hint"
            value={fields.upload_post_base_url}
            placeholder="https://api.upload-post.com/api"
            onChange={(event) => setFields((prev) => ({ ...prev, upload_post_base_url: event.target.value }))}
          />
          <FieldHint id="upload-post-base-url-hint">{t("dashboard:social_publish_base_url_hint")}</FieldHint>
        </div>
      </SettingsCard>

      <SettingsCard
        title={t("dashboard:social_publish_accounts_title")}
        description={t("dashboard:social_publish_accounts_desc")}
      >
        <div>
          <Button variant="outline" size="sm" onClick={() => void loadProfiles()} disabled={loadingProfiles}>
            {loadingProfiles ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <RefreshCw aria-hidden data-icon="inline-start" />
            )}
            {t("dashboard:social_publish_accounts_refresh")}
          </Button>
        </div>

        {profilesError && <InlineWarning message={profilesError} />}

        {profiles !== null && profiles.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("dashboard:social_publish_accounts_empty")}</p>
        )}

        {profiles !== null && profiles.length > 0 && (
          <div className="flex flex-col gap-3">
            {profiles.map((profile) => (
              <div key={profile.username} className="flex flex-col gap-1.5">
                <div className="font-mono text-xs text-muted-foreground">{profile.username}</div>
                <ul className="flex flex-wrap gap-1.5">
                  {profile.accounts.map((account) => (
                    <li key={account.platform} className="rounded-md border border-border px-2 py-1 text-xs">
                      <span>{account.platform}</span>
                      {account.handle && <span className="ml-1 text-muted-foreground">{account.handle}</span>}
                      {account.reauth_required && (
                        <span className="ml-1.5 text-warn">{t("dashboard:social_publish_reauth_required")}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </SettingsCard>

      <PageShellFooter>
        <SaveBar unit={unit} />
      </PageShellFooter>
    </div>
  );
}
