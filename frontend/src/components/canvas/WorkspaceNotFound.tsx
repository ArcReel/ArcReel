import { Link } from "wouter";
import { FileQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";
import { buttonVariants } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

/** 项目工作区里没有路由承接的地址：画布内显示空状态，外壳保留，可回到项目概览。 */
export function WorkspaceNotFound() {
  const { t } = useTranslation(["dashboard", "common"]);
  return (
    <div className="relative flex min-h-0 flex-1 overflow-y-auto">
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FileQuestion aria-hidden />
          </EmptyMedia>
          <EmptyTitle role="heading" aria-level={1}>{t("dashboard:workspace_not_found_title")}</EmptyTitle>
          <EmptyDescription>{t("common:not_found_description")}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Link href="/" replace className={buttonVariants({ variant: "outline" })}>
            {t("dashboard:workspace_not_found_back")}
          </Link>
        </EmptyContent>
      </Empty>
    </div>
  );
}
