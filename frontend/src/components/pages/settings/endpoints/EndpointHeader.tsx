import type { ReactNode } from "react";
import { Link } from "wouter";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { providerSettingsPath } from "@/app-routes";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button, buttonVariants } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** 从自定义供应商跳来时的返回目标（地址里的 `from` 参数解析出的供应商）。 */
export interface EndpointBackTarget {
  providerId: number;
  providerName: string;
}

interface EndpointHeaderProps {
  back: EndpointBackTarget | null;
  /** 标题；ComfyUI 端点的名称是可编辑字段，标题显示的是当前值。 */
  title: string;
  /** 标题后的类型与状态标记。 */
  badges?: ReactNode;
  /** 标题下一行：作者与版本、来源、节点数等。 */
  meta?: ReactNode;
  /** 页头主操作：「新建供应商并使用」「复制为我的端点」或「重新导入」。 */
  primary?: ReactNode;
  /** 更多操作菜单的条目；没有条目时不显示菜单按钮。 */
  menu?: ReactNode;
}

/** 端点详情的页头：返回原供应商、名称与标记、主操作与更多操作菜单。 */
export function EndpointHeader({ back, title, badges, meta, primary, menu }: EndpointHeaderProps) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-2">
      {back && (
        <div>
          <Link
            href={providerSettingsPath({ custom: back.providerId })}
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            <ArrowLeft aria-hidden data-icon="inline-start" />
            {t("ce_back_to_provider", { name: back.providerName })}
          </Link>
        </div>
      )}
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="min-w-0 text-lg font-medium">
              <TruncatedText text={title} />
            </h2>
            {badges}
          </div>
          {meta && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">{meta}</div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {primary}
          {menu && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label={t("ce_more_actions")} />}>
                <MoreHorizontal aria-hidden />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                {menu}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>
    </div>
  );
}
