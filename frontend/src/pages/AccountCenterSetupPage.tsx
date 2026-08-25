import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link2, Loader2, UserPlus } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import type { LoginResponse } from "@/api";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { useAuthStore } from "@/stores/auth-store";
import { safeReturnPath } from "@/utils/safe-url";

interface SetupInfo {
  username: string;
  display_name: string | null;
  roles: string[];
}

export function AccountCenterSetupPage() {
  const { t } = useTranslation("auth");
  const search = useSearch();
  const [, setLocation] = useLocation();
  const login = useAuthStore((state) => state.login);
  const submitting = useRef(false);
  const params = new URLSearchParams(search);
  const ticket = params.get("ticket") || "";
  const returnTo = safeReturnPath(params.get("return_to")) ?? "/app/projects";
  const [info, setInfo] = useState<SetupInfo | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState<"auto" | "bind" | "info" | null>(() => ticket ? "info" : null);
  const [error, setError] = useState(() => ticket ? "" : t("account_center_ticket_missing"));

  useEffect(() => {
    if (!ticket) return;
    const controller = new AbortController();
    void fetch(`/api/v1/auth/account-center/setup?ticket=${encodeURIComponent(ticket)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => ({})) as SetupInfo & {
          detail?: string | { message?: string };
        };
        if (!response.ok) {
          const detail = payload.detail;
          throw new Error(typeof detail === "string" ? detail : detail?.message || t("account_center_setup_failed"));
        }
        setInfo(payload);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : t("account_center_setup_failed"));
      })
      .finally(() => setLoading(null));
    return () => controller.abort();
  }, [t, ticket]);

  const complete = async (mode: "auto" | "bind") => {
    if (submitting.current || !ticket || !info) return;
    submitting.current = true;
    setError("");
    setLoading(mode);
    try {
      const response = await fetch("/api/v1/auth/account-center/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticket,
          mode,
          ...(mode === "bind" ? { username, password } : {}),
        }),
      });
      const payload = await response.json().catch(() => ({})) as LoginResponse & {
        detail?: string | { message?: string };
      };
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(typeof detail === "string" ? detail : detail?.message || t("account_center_setup_failed"));
      }
      login(
        payload.access_token,
        payload.user?.username ?? info?.username ?? "",
        payload.user?.role ?? "user",
        payload.user?.id ?? "",
        payload.user?.display_name ?? info?.display_name ?? null,
        payload.user?.identity_source ?? "account_center",
      );
      // Remove the one-time ticket from history so refresh/Back cannot submit
      // the completed binding a second time.
      setLocation(returnTo, { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("account_center_setup_failed"));
    } finally {
      submitting.current = false;
      setLoading(null);
    }
  };

  const handleBind = (event: FormEvent) => {
    event.preventDefault();
    void complete("bind");
  };

  return (
    <main className="min-h-screen bg-bg px-4 py-12 text-text">
      <section className="mx-auto w-full max-w-3xl">
        <div className="mb-7 text-center">
          <h1 className="text-2xl font-medium">{t("first_account_center_login")}</h1>
          {info ? (
            <p className="mt-2 text-sm text-text-3">
              {info.display_name || info.username} · {info.roles.join(" / ")}
            </p>
          ) : null}
        </div>

        {loading === "info" ? (
          <div role="status" className="flex justify-center gap-2 text-sm text-text-3">
            <Loader2 className="h-4 w-4 motion-safe:animate-spin" /> {t("loading_account_info")}
          </div>
        ) : (
          <div className="grid gap-5 md:grid-cols-2">
            <article className="rounded-2xl border border-accent/30 bg-accent-dim/35 p-6">
              <UserPlus className="h-6 w-6 text-accent-2" />
              <h2 className="mt-4 text-lg font-medium">{t("auto_create_account")}</h2>
              <p className="mt-2 min-h-12 text-sm leading-6 text-text-3">{t("auto_create_account_desc")}</p>
              <button
                type="button"
                disabled={!info || loading !== null}
                onClick={() => void complete("auto")}
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-black disabled:opacity-50"
              >
                {loading === "auto" ? <Loader2 className="h-4 w-4 motion-safe:animate-spin" /> : null}
                {t("create_and_enter")}
              </button>
            </article>

            <article className="rounded-2xl border border-hairline bg-bg-grad-a/70 p-6">
              <Link2 className="h-6 w-6 text-text-2" />
              <h2 className="mt-4 text-lg font-medium">{t("bind_existing_account")}</h2>
              <p className="mt-2 text-sm leading-6 text-text-3">{t("bind_existing_account_desc")}</p>
              <form onSubmit={handleBind} className="mt-4 space-y-3">
                <div>
                  <FieldLabel htmlFor="bind-username">{t("username")}</FieldLabel>
                  <input id="bind-username" value={username} onChange={(e) => setUsername(e.target.value)} className={INPUT_CLS} required />
                </div>
                <div>
                  <FieldLabel htmlFor="bind-password">{t("password")}</FieldLabel>
                  <input id="bind-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT_CLS} required />
                </div>
                <button
                  type="submit"
                  disabled={!info || loading !== null}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-hairline px-4 py-2.5 text-sm text-text-2 hover:text-text disabled:opacity-50"
                >
                  {loading === "bind" ? <Loader2 className="h-4 w-4 motion-safe:animate-spin" /> : null}
                  {t("verify_bind_and_enter")}
                </button>
              </form>
            </article>
          </div>
        )}
        {error ? <p role="alert" className="mt-5 text-center text-sm text-warm-bright">{error}</p> : null}
      </section>
    </main>
  );
}
