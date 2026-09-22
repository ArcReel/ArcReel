import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";

export function NotFoundPage() {
  const [, navigate] = useLocation();
  const { t } = useTranslation("common");

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg px-4 text-text animate-[fadeIn_0.5s_ease-out]">
      <h1 className="font-editorial text-[8rem] font-extralight leading-none tracking-tighter text-text-4">
        404
      </h1>
      <p className="mt-4 text-[15px] text-text-3">{t("not_found_title")}</p>
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
