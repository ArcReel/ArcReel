import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { errMsg, voidPromise } from "@/utils/async";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { safeReturnPath } from "@/utils/safe-url";
import { BRAND } from "@/branding";
import type { LoginResponse, ErrorResponse } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginPage() {
  const { t, i18n } = useTranslation(["common", "auth"]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [, setLocation] = useLocation();
  const search = useSearch();
  const login = useAuthStore((s) => s.login);
  const usernameRef = useAutoFocus<HTMLInputElement>();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const body = new URLSearchParams({
        username,
        password,
        grant_type: "password",
      });
      const resp = await fetch("/api/v1/auth/token", {
        method: "POST",
        headers: {
          "Accept-Language": i18n.language || "zh",
        },
        body,
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({})) as Partial<ErrorResponse>;
        const detail = data.detail;
        throw new Error(typeof detail === "string" ? detail : t("auth:login_failed"));
      }

      const data = await resp.json() as LoginResponse;
      login(data.access_token, username);
      // 登录成功后回跳到进入登录页前的原始地址（由 AuthGuard / 401 拦截以 ?from 传入），
      // 经 safeReturnPath 校验为站内安全路径；非法或缺失时回退到项目列表。
      const returnTo = safeReturnPath(new URLSearchParams(search).get("from"));
      setLocation(returnTo ?? "/app/projects");
    } catch (err) {
      setError(errMsg(err, t("auth:login_failed")));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div data-testid="login-page" className="relative flex h-dvh overflow-y-auto bg-background p-4 text-foreground">
      <div className="m-auto flex w-full max-w-sm flex-col gap-6 rounded-xl border bg-card p-8 shadow-overlay">
        <div className="flex flex-col items-center gap-1.5 text-center">
          <h1 className="flex items-center gap-2 font-editorial text-3xl tracking-tight">
            <picture>
              <source media="(prefers-reduced-motion: reduce)" srcSet="/logo.svg" />
              <img src="/logo-animated.svg" alt="" aria-hidden className="block size-7" />
            </picture>
            <span>{BRAND.name}</span>
          </h1>
          <p className="text-xs font-medium text-muted-foreground">{t("auth:login_subtitle")}</p>
        </div>

        <form onSubmit={voidPromise(handleSubmit)} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="login-username">{t("auth:username")}</Label>
            <Input
              id="login-username"
              type="text"
              autoComplete="username"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              ref={usernameRef}
              required
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="login-password">{t("auth:password")}</Label>
            <Input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {error}
            </p>
          )}

          <Button type="submit" size="lg" disabled={loading}>
            {loading && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {loading ? t("auth:logging_in") : t("auth:login")}
          </Button>
        </form>
      </div>
    </div>
  );
}
