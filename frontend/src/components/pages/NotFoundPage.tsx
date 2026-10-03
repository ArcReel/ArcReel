import { Link } from "wouter";
import { useTranslation } from "react-i18next";
import { ROUTE_APP_PROJECTS } from "@/app-routes";
import { buttonVariants } from "@/components/ui/button";

export function NotFoundPage() {
  const { t } = useTranslation("common");

  return (
    <main className="relative flex h-dvh overflow-y-auto bg-background p-4 text-foreground">
      <div className="m-auto flex flex-col items-center gap-3 text-center">
        <p aria-hidden className="font-editorial text-9xl leading-none tracking-tighter text-muted-foreground">
          404
        </p>
        <h1 className="text-lg font-medium">{t("not_found_title")}</h1>
        <p className="text-sm text-muted-foreground">{t("not_found_description")}</p>
        <Link href={`~${ROUTE_APP_PROJECTS}`} replace className={buttonVariants({ variant: "outline", className: "mt-5" })}>
          {t("not_found_back")}
        </Link>
      </div>
    </main>
  );
}
