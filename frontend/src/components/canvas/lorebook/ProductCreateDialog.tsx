import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** 新增商品：名称、描述与品牌。提交失败时对话框保持打开，错误提示由上层回调发出。 */
export function ProductCreateDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: { name: string; description: string; brand: string }) => Promise<void>;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [brand, setBrand] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const formId = useId();
  const fieldId = useId();

  const canSubmit = !submitting && name.trim() !== "";

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({ name: name.trim(), description: description.trim(), brand: brand.trim() });
    } catch {
      // 失败时保持对话框打开
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        // 提交在途时忽略 Esc 与遮罩点击
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent showCloseButton={!submitting}>
        <DialogHeader>
          <DialogTitle>{t("dashboard:add_product")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <form
            id={formId}
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleSubmit();
            }}
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${fieldId}-name`}>{t("dashboard:ad_init_product_name_label")}</Label>
              <Input
                id={`${fieldId}-name`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${fieldId}-description`}>{t("dashboard:description")}</Label>
              <Textarea
                id={`${fieldId}-description`}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={submitting}
                rows={3}
                placeholder={t("dashboard:product_desc_placeholder")}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${fieldId}-brand`}>{t("dashboard:product_brand_label")}</Label>
              <Input
                id={`${fieldId}-brand`}
                value={brand}
                onChange={(e) => setBrand(e.target.value)}
                disabled={submitting}
                placeholder={t("dashboard:product_brand_placeholder")}
              />
            </div>
          </form>
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />} disabled={submitting}>
            {t("common:cancel")}
          </DialogClose>
          <Button type="submit" form={formId} disabled={!canSubmit}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {submitting ? t("common:saving") : t("common:save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
