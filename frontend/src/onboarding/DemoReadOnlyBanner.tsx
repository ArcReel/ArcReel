import { useTranslation } from "react-i18next";

/**
 * 演示工作台顶部的一条说明。
 *
 * 演示态下界面上大量控件不渲染或不可用，逐个加提示会把界面钉满解释；这条横幅一次讲清
 * 「你在看一份示例数据，写操作都不可用」，其余控件保持安静。取片头引导条的形态：左端一道
 * accent 竖线、极淡的 accent 渐变、底部一条 hairline 与内容区分开，没有图标、关闭按钮和动画 ——
 * 它是状态说明，不是需要处理的通知。
 */
export function DemoReadOnlyBanner() {
  const { t } = useTranslation("onboarding");

  return (
    <div
      role="status"
      className="flex shrink-0 items-baseline gap-2.5 border-b border-hairline bg-accent-dim px-4 py-2 text-[12.5px]"
      style={{
        boxShadow: "inset 3px 0 0 var(--color-accent)",
      }}
    >
      <span className="font-semibold tracking-wide text-text">
        {t("demo_banner_title")}
      </span>
      <span className="text-text-3">{t("demo_banner_body")}</span>
    </div>
  );
}
