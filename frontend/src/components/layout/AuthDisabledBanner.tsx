import { ShieldOff } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useAuthStore } from "@/stores/auth-store";

/** 提示条高度写入的 CSS 变量；`.h-app-screen` 等工具类据此从视口高度里扣除提示条。 */
const BANNER_HEIGHT_VAR = "--auth-banner-h";

/**
 * 后端认证关闭（``AUTH_ENABLED=false``）时挂在应用最顶部的常驻提示条。
 *
 * 这是持续存在的部署状态而非一次性通知，所以没有关闭按钮；形态取工作台 warm 提示条。
 * 渲染期间把自身高度写入 ``--auth-banner-h``，定高布局与 sticky 侧栏据此让出空间。
 */
export function AuthDisabledBanner() {
  const { t } = useTranslation("auth");
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const ref = useRef<HTMLDivElement>(null);
  const visible = authEnabled === false;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!visible || !el) return;
    const root = document.documentElement;
    const sync = () => root.style.setProperty(BANNER_HEIGHT_VAR, `${el.offsetHeight}px`);
    sync();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      root.style.removeProperty(BANNER_HEIGHT_VAR);
    };
  }, [visible]);

  if (!visible) return null;

  return (
    <div
      ref={ref}
      role="status"
      className="sticky top-0 z-50 flex items-start gap-2.5 border-b px-5 py-2"
      style={{ borderColor: "var(--color-warm-ring)", background: "linear-gradient(var(--color-warm-soft), var(--color-warm-soft)), var(--color-bg)" }}
    >
      <ShieldOff
        className="mt-0.5 h-4 w-4 shrink-0"
        style={{ color: "var(--color-warm)" }}
        aria-hidden="true"
      />
      <p className="m-0 min-w-0 flex-1 text-[12px] leading-[1.55] text-[var(--color-text-2)]">
        <span className="font-semibold text-[var(--color-text)]">{t("auth_disabled_banner_title")}</span>{" "}
        {t("auth_disabled_banner_body")}
      </p>
    </div>
  );
}
