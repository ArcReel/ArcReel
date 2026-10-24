/** 模版分类的显示名；未翻译的分类显示原值。 */
export function categoryLabel(t: (key: string, options: { defaultValue: string }) => string, category: string) {
  return t(`prompt_templates_category_${category}`, { defaultValue: category });
}
