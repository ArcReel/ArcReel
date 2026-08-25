import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import type { LoginResponse } from "@/api";
import { useAuthStore } from "@/stores/auth-store";
import { safeReturnPath } from "@/utils/safe-url";

export function AccountCenterCallbackPage() {
  const { t } = useTranslation("auth");
  const search = useSearch();
  const [, setLocation] = useLocation();
  const login = useAuthStore((state) => state.login);
  const started = useRef(false);
  const ticket = new URLSearchParams(search).get("ticket") || "";
  const [error, setError] = useState(() => ticket ? "" : t("account_center_ticket_missing"));

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(search);
    const returnTo = safeReturnPath(params.get("return_to")) ?? "/app/projects";
    if (!ticket) return;

    void fetch("/api/v1/auth/account-center/exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => ({})) as LoginResponse & {
          detail?: string | { message?: string };
        };
        if (!response.ok) {
          const detail = payload.detail;
          throw new Error(typeof detail === "string" ? detail : detail?.message || t("account_center_login_failed"));
        }
        login(
          payload.access_token,
          payload.user?.username ?? "",
          payload.user?.role ?? "user",
          payload.user?.id ?? "",
          payload.user?.display_name ?? null,
          payload.user?.identity_source ?? "account_center",
        );
        // Keep the consumed one-time ticket out of browser history.
        setLocation(returnTo, { replace: true });
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : t("account_center_login_failed"));
      });
  }, [login, search, setLocation, t, ticket]);

  return <AccountCenterStatus error={error} loadingText={t("account_center_signing_in")} />;
}

export function AccountCenterStatus({ error, loadingText }: { error: string; loadingText: string }) {
  const { t } = useTranslation("auth");
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 text-text">
      <section className="w-full max-w-md rounded-2xl border border-hairline bg-bg-grad-a/85 p-8 text-center shadow-2xl">
        {error ? (
          <>
            <h1 className="text-xl font-medium">{t("account_center_login_failed")}</h1>
            <p className="mt-3 text-sm text-warm-bright">{error}</p>
            <button
              type="button"
              onClick={() => window.location.assign("/login")}
              className="mt-6 rounded-lg border border-hairline px-4 py-2 text-sm text-text-2 hover:text-text"
            >
              {t("back_to_login")}
            </button>
          </>
        ) : (
          <div role="status" className="flex items-center justify-center gap-3 text-sm text-text-3">
            <Loader2 className="h-5 w-5 motion-safe:animate-spin" />
            <span>{loadingText}</span>
          </div>
        )}
      </section>
    </main>
  );
}
