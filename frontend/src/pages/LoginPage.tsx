import { useState, type CSSProperties, type FormEvent } from "react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { errMsg, voidPromise } from "@/utils/async";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import type { LoginResponse, ErrorResponse } from "@/api";
import { FieldLabel } from "@/components/ui/FieldLabel";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";

const POSTER_GRID_STYLE: CSSProperties = {
  backgroundImage:
    "linear-gradient(oklch(1 0 0) 1px, transparent 1px), linear-gradient(90deg, oklch(1 0 0) 1px, transparent 1px)",
  backgroundSize: "44px 44px",
  maskImage: "radial-gradient(60% 60% at 50% 35%, black, transparent)",
  WebkitMaskImage: "radial-gradient(60% 60% at 50% 35%, black, transparent)",
  opacity: 0.05,
};

const AMBIENT_GLOW_STYLE: CSSProperties = {
  background:
    "radial-gradient(circle at 50% 0%, oklch(0.76 0.09 295 / 0.16), transparent 60%)",
};

export function LoginPage() {
  const { t, i18n } = useTranslation(["common", "auth"]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [, setLocation] = useLocation();
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
      setLocation("/app/projects");
    } catch (err) {
      setError(errMsg(err, t("auth:login_failed")));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg px-4 text-text">
      <div aria-hidden className="pointer-events-none absolute inset-0" style={AMBIENT_GLOW_STYLE} />
      <div aria-hidden className="pointer-events-none absolute inset-0" style={POSTER_GRID_STYLE} />

      <div
        className="relative w-full max-w-sm overflow-hidden rounded-2xl border border-hairline p-8 shadow-2xl"
        style={CARD_STYLE}
      >
        <div className="mb-6 text-center">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-text-4">
            system · login
          </div>
          <h1 className="font-editorial mt-1 flex items-center justify-center gap-2 text-[28px] tracking-tight text-text">
            <img src="/android-chrome-192x192.png" alt="ArcReel" className="h-7 w-7" />
            <span>ArcReel</span>
          </h1>
        </div>

        <form onSubmit={voidPromise(handleSubmit)} className="space-y-4">
          <div>
            <FieldLabel htmlFor="login-username" required>
              {t("auth:username")}
            </FieldLabel>
            <input
              id="login-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={INPUT_CLS}
              ref={usernameRef}
              required
            />
          </div>

          <div>
            <FieldLabel htmlFor="login-password" required>
              {t("auth:password")}
            </FieldLabel>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT_CLS}
              required
            />
          </div>

          {error && (
            <p className="text-sm text-warm-bright">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className={`${ACCENT_BTN_CLS} w-full justify-center`}
            style={ACCENT_BUTTON_STYLE}
          >
            {loading ? t("auth:logging_in") : t("auth:login")}
          </button>
        </form>
      </div>
    </div>
  );
}
