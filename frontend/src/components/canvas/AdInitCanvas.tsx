import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, ShoppingBag, Sparkles, X } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

interface AdInitCanvasProps {
  projectName: string;
  /** 提交成功后调用（通常刷新项目数据，使页面切换到概览态）。 */
  onDone: () => void | Promise<void>;
}

const CARD_BG =
  "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))";
const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

/**
 * 广告/短片项目初始化页：上传多张产品图、填写产品描述与创作 brief，
 * 可勾选「生成标准产品参考图」触发 product sheet 生成（走资产生成队列）。
 * 也支持不上传产品、只写 brief 的通用短片流程。
 */
export function AdInitCanvas({ projectName, onDone }: AdInitCanvasProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [productName, setProductName] = useState("");
  const [description, setDescription] = useState("");
  const [brief, setBrief] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [generateSheet, setGenerateSheet] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const nameId = useId();
  const imagesId = useId();
  const descId = useId();
  const briefId = useId();
  const sheetId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const hasProduct = productName.trim() !== "" && description.trim() !== "";
  const canSubmit = !submitting && (brief.trim() !== "" || hasProduct);

  const handleFilesChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (selected.length === 0) return;
    setFiles((prev) => [...prev, ...selected]);
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const name = productName.trim();
      const desc = description.trim();
      if (hasProduct) {
        await API.addProjectProduct(projectName, name, desc);
        for (const file of files) {
          await API.uploadFile(projectName, "product_ref", file, name);
        }
      }
      if (brief.trim()) {
        await API.updateProject(projectName, { brief: brief.trim() });
      }
      if (generateSheet && hasProduct) {
        await API.generateProjectProduct(projectName, name, desc);
      }
      useAppStore.getState().pushToast(t("dashboard:ad_init_success_toast"), "success");
      await onDone();
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("dashboard:ad_init_failed", { message: errMsg(err) }), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section
      className="relative overflow-hidden rounded-2xl p-6"
      style={{
        border: "1px solid var(--color-hairline-soft)",
        background: CARD_BG,
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)",
      }}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      <header className="mb-5">
        <div className="flex items-center gap-2.5">
          <Sparkles className="h-4 w-4" style={{ color: "var(--color-accent-2)" }} />
          <h2
            className="display-serif text-[18px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("dashboard:ad_init_title")}
          </h2>
        </div>
        <p className="mt-1 text-[12.5px]" style={{ color: "var(--color-text-3)" }}>
          {t("dashboard:ad_init_subtitle")}
        </p>
      </header>

      {/* ---- 产品信息 ---- */}
      <fieldset
        className="mb-5 rounded-xl p-4"
        style={{ border: "1px solid var(--color-hairline-soft)" }}
      >
        <legend
          className="flex items-center gap-1.5 px-1 text-[10.5px] font-bold uppercase"
          style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
        >
          <ShoppingBag className="h-3 w-3" />
          {t("dashboard:ad_init_product_section")}
        </legend>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor={nameId}>{t("dashboard:ad_init_product_name_label")}</FieldLabel>
            <input
              id={nameId}
              type="text"
              value={productName}
              onChange={(e) => setProductName(e.target.value)}
              disabled={submitting}
              className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none"
              style={FIELD_STYLE}
            />
          </div>
          <div>
            <FieldLabel htmlFor={imagesId}>{t("dashboard:ad_init_product_images_label")}</FieldLabel>
            <input
              ref={fileInputRef}
              id={imagesId}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              multiple
              disabled={submitting}
              className="hidden"
              onChange={handleFilesChange}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={submitting}
              className="focus-ring mt-1.5 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-2 text-[12.5px] transition-colors hover:border-[var(--color-accent-soft)]"
              style={{ color: "var(--color-text-3)" }}
            >
              <ImagePlus className="h-3.5 w-3.5" />
              {files.length > 0
                ? t("dashboard:ad_init_images_selected", { count: files.length })
                : t("dashboard:product_upload_refs")}
            </button>
            <p className="mt-1 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
              {t("dashboard:ad_init_product_images_hint")}
            </p>
            {files.length > 0 && (
              <ul className="mt-1.5 space-y-1">
                {files.map((file, idx) => (
                  <li
                    key={`${file.name}-${idx}`}
                    className="flex items-center gap-2 rounded-md px-2 py-1 text-[11.5px]"
                    style={{
                      border: "1px solid var(--color-hairline-soft)",
                      color: "var(--color-text-2)",
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">{file.name}</span>
                    <button
                      type="button"
                      onClick={() => setFiles((prev) => prev.filter((_, i) => i !== idx))}
                      disabled={submitting}
                      aria-label={`${t("common:delete")} ${file.name}`}
                      className="focus-ring inline-flex h-5 w-5 shrink-0 items-center justify-center rounded transition-colors hover:bg-[oklch(1_0_0_/_0.06)]"
                      style={{ color: "var(--color-text-4)" }}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="mt-3">
          <FieldLabel htmlFor={descId}>{t("dashboard:ad_init_product_desc_label")}</FieldLabel>
          <textarea
            id={descId}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={submitting}
            rows={3}
            className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.6] outline-none"
            style={FIELD_STYLE}
            placeholder={t("dashboard:product_desc_placeholder")}
          />
        </div>

        <div className="mt-2 flex items-start gap-2">
          <input
            id={sheetId}
            type="checkbox"
            checked={generateSheet}
            onChange={(e) => setGenerateSheet(e.target.checked)}
            disabled={submitting || !hasProduct}
            className="focus-ring mt-0.5 h-3.5 w-3.5 accent-[var(--color-accent)]"
          />
          <div>
            <label
              htmlFor={sheetId}
              className="block cursor-pointer select-none text-[12.5px]"
              style={{ color: "var(--color-text-2)" }}
            >
              {t("dashboard:ad_init_generate_sheet_label")}
            </label>
            <p className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
              {t("dashboard:ad_init_generate_sheet_hint")}
            </p>
          </div>
        </div>
      </fieldset>

      {/* ---- 创作 Brief ---- */}
      <div className="mb-5">
        <FieldLabel htmlFor={briefId}>{t("dashboard:ad_init_brief_label")}</FieldLabel>
        <textarea
          id={briefId}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          disabled={submitting}
          rows={4}
          className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.6] outline-none"
          style={FIELD_STYLE}
          placeholder={t("dashboard:ad_init_brief_placeholder")}
        />
      </div>

      <button
        type="button"
        onClick={() => void handleSubmit()}
        disabled={!canSubmit}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-[13px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          color: "oklch(0.14 0 0)",
          background:
            "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
        }}
      >
        {submitting ? t("dashboard:ad_init_submitting") : t("dashboard:ad_init_submit")}
      </button>
    </section>
  );
}

function FieldLabel({
  children,
  htmlFor,
}: {
  children: React.ReactNode;
  htmlFor: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[10px] font-semibold uppercase tracking-[0.12em]"
      style={{ color: "var(--color-text-4)" }}
    >
      {children}
    </label>
  );
}
