import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { GHOST_BTN_LG_CLS } from "@/components/shared/darkroom-tokens";

export function NotFoundPage() {
  const [, navigate] = useLocation();
  const { t } = useTranslation("common");

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4 text-foreground animate-[fadeIn_0.5s_ease-out]">
      <h1 className="font-editorial text-[8rem] font-extralight leading-none tracking-tighter text-muted-foreground">
        404
      </h1>
      <p className="mt-4 text-[15px] text-muted-foreground">{t("not_found_title")}</p>
      <button
        type="button"
        onClick={() => navigate("/app/projects", { replace: true })}
        className={`mt-8 ${GHOST_BTN_LG_CLS}`}
      >
        {t("not_found_back")}
      </button>
    </div>
  );
}
