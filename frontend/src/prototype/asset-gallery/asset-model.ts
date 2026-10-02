// PROTOTYPE — #2973：把角色、场景、道具、商品归一成同一个视图模型，
// 验证「四类资产卡合并为同一组件」时，差异能否只靠「按类型开关的字段区块」表达。

import { API } from "@/api";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types/asset-sheet";
import type { Character, Product, Prop, Scene } from "@/types";

export interface ProtoDerivative {
  name: string;
  description: string;
  sheetUrl: string | null;
  referenced: boolean;
  /** 原型注入的样例：本地数据里没有衍生，用本体图冒充展示。 */
  sample?: boolean;
}

export interface ProtoAsset {
  type: AssetSheetType;
  name: string;
  description: string;
  /** 资产图在版本接口里的资源类型（characters / scenes / props / products）。 */
  versionResource: string;
  sheetPath: string | null;
  sheetUrl: string | null;
  aliases: string[];
  /** 角色：原图（单张）；商品：商品原图（多张）。 */
  referenceUrls: string[];
  voiceStyle?: string;
  audioUrl?: string | null;
  brand?: string;
  sellingPoints?: string[];
  derivatives: ProtoDerivative[];
}

export type ProtoStatus = "generating" | "missing" | "stale" | "no-description" | "current";

export function statusOf(asset: ProtoAsset, row: AssetSheetStatusRow | undefined, generating: boolean): ProtoStatus {
  if (generating) return "generating";
  if (!asset.description.trim()) return "no-description";
  if (row?.status === "missing" || !asset.sheetPath) return "missing";
  if (row?.status === "stale") return "stale";
  return "current";
}

export const STATUS_LABEL: Record<ProtoStatus, string> = {
  generating: "生成中",
  missing: "待生成",
  stale: "已过期",
  "no-description": "缺描述",
  current: "",
};

export const TYPE_LABEL: Record<AssetSheetType, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  product: "商品",
};

const SAMPLE_DERIVATIVES: { name: string; description: string; referenced: boolean }[] = [
  { name: "便装", description: "换成灰色连帽卫衣与牛仔裤，去掉笔记本，其余外貌不变。", referenced: true },
  { name: "二十年前", description: "年轻二十岁：面部更瘦，头发浓密，穿 2000 年代的白衬衫。", referenced: false },
];

type Fp = (path: string) => number | null;

function url(projectName: string, path: string | null | undefined, fp: Fp): string | null {
  return path ? API.getFileUrl(projectName, path, fp(path)) : null;
}

export function buildAssets(
  projectName: string,
  type: AssetSheetType,
  data: Record<string, Character | Scene | Prop | Product>,
  fp: Fp,
): ProtoAsset[] {
  return Object.entries(data).map(([name, raw], index) => {
    if (type === "character") {
      const c = raw as Character;
      const sheetUrl = url(projectName, c.character_sheet, fp);
      const real = Object.entries(c.derivatives ?? {}).map(([dn, d]) => ({
        name: dn,
        description: d.description,
        sheetUrl: url(projectName, d.character_sheet, fp),
        referenced: Boolean(d.referenced),
      }));
      // 本地项目没有衍生：给前两个角色注入样例，只为看衍生区块的形态
      const derivatives =
        real.length > 0 || index > 1
          ? real
          : SAMPLE_DERIVATIVES.slice(0, index === 0 ? 2 : 1).map((d) => ({
              ...d,
              sheetUrl: d.referenced ? sheetUrl : null,
              sample: true,
            }));
      return {
        type,
        name,
        description: c.description,
        versionResource: "characters",
        sheetPath: c.character_sheet ?? null,
        sheetUrl,
        aliases: c.aliases ?? [],
        referenceUrls: c.reference_image ? [url(projectName, c.reference_image, fp)!] : [],
        voiceStyle: c.voice_style ?? "",
        audioUrl: url(projectName, c.reference_audio, fp),
        derivatives,
      };
    }
    if (type === "product") {
      const p = raw as Product;
      return {
        type,
        name,
        description: p.description,
        versionResource: "products",
        sheetPath: p.product_sheet ?? null,
        sheetUrl: url(projectName, p.product_sheet, fp),
        aliases: [],
        referenceUrls: (p.reference_images ?? []).map((r) => url(projectName, r, fp)!),
        brand: p.brand ?? "",
        sellingPoints: p.selling_points ?? [],
        derivatives: [],
      };
    }
    const s = raw as Scene | Prop;
    const sheet = type === "scene" ? (s as Scene).scene_sheet : (s as Prop).prop_sheet;
    return {
      type,
      name,
      description: s.description,
      versionResource: type === "scene" ? "scenes" : "props",
      sheetPath: sheet ?? null,
      sheetUrl: url(projectName, sheet, fp),
      aliases: s.aliases ?? [],
      referenceUrls: [],
      derivatives: [],
    };
  });
}
