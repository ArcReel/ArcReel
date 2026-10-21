import type { ComponentType, SVGProps } from "react";
import { translate } from "@docusaurus/Translate";

export interface Sponsor {
  name: string;
  // 内联 SVG 组件，用 currentColor 跟随明暗主题的文字色。
  Logo: ComponentType<SVGProps<SVGSVGElement>>;
  url: string;
  description: string;
}

// viewBox 比图形外框大一圈，在圆形头像里留出内边距。
function ApimartLogo(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="280 271 1500 1500" fill="currentColor" aria-hidden="true" {...props}>
      <path d="M 508 528 L 509 1514 L 586 1514 L 588 1512 L 587 610 L 589 607 L 670 607 L 1022 1009 L 1027 1007 L 1388 607 L 1467 607 L 1469 609 L 1470 738 L 1469 1513 L 1551 1513 L 1551 528 L 1350 528 L 1026 901 L 841 687 L 707 528 Z" />
      <path d="M 627 646 L 626 1513 L 699 1513 L 700 785 L 1008 1131 L 1025 1147 L 1359 781 L 1360 1513 L 1434 1514 L 1435 646 L 1394 647 L 1240 821 L 1024 1059 L 1009 1045 L 789 795 L 661 646 Z" />
      <path d="M 732 872 L 732 1513 L 802 1514 L 805 1512 L 805 1052 L 808 1053 L 888 1145 L 1024 1293 L 1250 1047 L 1250 1513 L 1322 1514 L 1324 1512 L 1323 872 L 1304 890 L 1026 1196 L 1021 1195 Z" />
    </svg>
  );
}

// translate() 须在渲染期调用才能取到当前 locale 的译文，所以用函数而不是模块级常量。
export function getSponsors(): Sponsor[] {
  return [
    {
      name: "APIMart",
      Logo: ApimartLogo,
      url: "https://go.apimart.ai/tw-arcreel",
      description: translate({
        id: "sponsors.apimart.description",
        message: "专注 AI 图片/视频生成的低价 API 平台，GPT-Image-2 低至 $0.006/张，一套异步 API 通吃图片与视频。",
        description: "APIMart description in the navbar sponsor card",
      }),
    },
  ];
}

export const BECOME_SPONSOR_URL = "mailto:support@arc-reel.com";
