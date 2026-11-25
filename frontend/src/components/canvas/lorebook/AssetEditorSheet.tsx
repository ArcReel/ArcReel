import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
import { EditUnitRetentionContext } from "@/components/shared/edit-unit/edit-unit-retention";
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types";
import { AssetCreateForm } from "./AssetCreateForm";
import { hasActiveAssetName } from "./assetBusyGuard";
import { AssetDetailEditor } from "./AssetDetailEditor";
import type { GalleryAssetSource } from "./gallery-model";

export type AssetEditorTarget = { mode: "edit"; name: string } | { mode: "create" };

export interface AssetEditorSheetProps {
  projectName: string;
  assetType: AssetSheetType;
  assets: Record<string, GalleryAssetSource>;
  target: AssetEditorTarget | null;
  onTargetChange: (target: AssetEditorTarget | null) => void;
  order?: readonly string[];
  statusByName: ReadonlyMap<string, AssetSheetStatusRow>;
  derivativeReferencesByCharacter?: ReadonlyMap<string, ReadonlyMap<string, boolean | null>>;
  generatingNames?: ReadonlySet<string>;
  writingNames?: ReadonlySet<string>;
  readOnly: boolean;
  onGenerate: (name: string) => unknown;
}

export function AssetEditorSheet({
  projectName,
  assetType,
  assets,
  target,
  onTargetChange,
  order,
  statusByName,
  derivativeReferencesByCharacter,
  generatingNames,
  writingNames,
  readOnly,
  onGenerate,
}: AssetEditorSheetProps) {
  const { t } = useTranslation(["assets", "common"]);
  const confirmLeave = useConfirmLeave();
  const [renamed, setRenamed] = useState<{ from: string; to: string } | null>(null);
  const targetKey = target === null ? null : target.mode === "create" ? "create" : `edit:${target.name}`;
  const [session, setSession] = useState({ key: targetKey, id: 0 });
  if (session.key !== targetKey) {
    const selfRename =
      renamed !== null && targetKey === `edit:${renamed.to}` && session.key === `edit:${renamed.from}`;
    setSession({ key: targetKey, id: selfRename || targetKey === null ? session.id : session.id + 1 });
    if (!selfRename && renamed !== null) setRenamed(null);
  }
  if (renamed !== null && session.key === `edit:${renamed.to}` && assets[renamed.to]) setRenamed(null);

  const name = target?.mode === "edit" ? target.name : null;
  const live =
    name === null ? undefined : (assets[name] ?? (renamed?.to === name ? assets[renamed.from] : undefined));
  const [last, setLast] = useState<{ name: string; asset: GalleryAssetSource } | null>(null);
  if (name !== null && live && (last?.name !== name || last.asset !== live)) setLast({ name, asset: live });
  const [held, setHeld] = useState(false);
  const retention = useMemo(() => ({ protect: (_key: string, value: boolean) => setHeld(value) }), []);
  const [submitting, setSubmitting] = useState(false);

  const present = target?.mode === "create" || live !== undefined;
  const open = target !== null && (present || held);
  useEffect(() => {
    // react-doctor-disable-next-line react-doctor/no-prop-callback-in-effect -- 资产被外部删除（项目数据刷新）且编辑单元不再保留时才关闭，两个条件分别来自数据推送与子编辑单元，没有单一事件可承载；这里清掉父级的目标，避免同名资产再出现时自动重开
    if (target !== null && !present && !held) onTargetChange(null);
  }, [target, present, held, onTargetChange]);

  const names = Object.keys(assets);
  const sequence = name !== null && order?.includes(name) ? order : names;
  const index = name === null ? -1 : sequence.indexOf(name);
  const previous = index > 0 ? sequence[index - 1] : undefined;
  const next = index >= 0 && index < sequence.length - 1 ? sequence[index + 1] : undefined;
  const switchTo = (to: string) =>
    confirmLeave(() => onTargetChange({ mode: "edit", name: to }), { saveLabel: t("common:save_and_switch") });

  const handleRenamed = useCallback(
    (from: string, to: string) => {
      setRenamed({ from, to });
      onTargetChange({ mode: "edit", name: to });
    },
    [onTargetChange],
  );
  const handleCreated = useCallback((created: string) => onTargetChange({ mode: "edit", name: created }), [onTargetChange]);
  const close = useCallback(() => onTargetChange(null), [onTargetChange]);

  const navigation =
    names.length > 1 && index >= 0 ? (
      <div className="flex shrink-0 items-center">
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={previous === undefined}
          onClick={() => previous !== undefined && switchTo(previous)}
          aria-label={t("assets:editor_previous")}
        >
          <ChevronUp aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={next === undefined}
          onClick={() => next !== undefined && switchTo(next)}
          aria-label={t("assets:editor_next")}
        >
          <ChevronDown aria-hidden />
        </Button>
      </div>
    ) : null;

  const shownName = name ?? last?.name ?? null;
  const shownAsset = live ?? (shownName !== null && last?.name === shownName ? last.asset : undefined);

  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen || (submitting && target?.mode === "create")) return;
        confirmLeave(close);
      }}
    >
      <SheetContent side="right" className="data-[side=right]:w-140">
        {target?.mode === "create" ? (
          <AssetCreateForm
            key={session.id}
            projectName={projectName}
            assetType={assetType}
            onCancel={() => confirmLeave(close)}
            onCreated={handleCreated}
            onSubmittingChange={setSubmitting}
          />
        ) : shownName !== null && shownAsset ? (
          <EditUnitRetentionContext.Provider value={retention}>
            <RetainedEditUnit
              identity={live ? `${session.id}` : `${session.id}:removed`}
              value={{ name: shownName, asset: shownAsset }}
              message={t("assets:editor_asset_removed")}
            >
              {(shown) => (
                <AssetDetailEditor
                  key={session.id}
                  projectName={projectName}
                  assetType={assetType}
                  name={shown.name}
                  asset={shown.asset}
                  sheetStatus={statusByName.get(shown.name)}
                  derivativeReferences={derivativeReferencesByCharacter?.get(shown.name)}
                  generating={hasActiveAssetName(generatingNames, shown.name)}
                  writing={writingNames?.has(shown.name) ?? false}
                  readOnly={readOnly}
                  onGenerate={onGenerate}
                  onRenamed={handleRenamed}
                  navigation={navigation}
                />
              )}
            </RetainedEditUnit>
          </EditUnitRetentionContext.Provider>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
