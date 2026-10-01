import type { EndpointDefinition } from "@/types";
import genericImageSubmitPoll from "@/data/example-templates/generic-image-submit-poll.json";
import genericSubmitPoll from "@/data/example-templates/generic-submit-poll.json";

/** 新建端点表单可选的示例模板：每种媒体类型一份「提交 + 轮询」骨架，选中后整份替换草稿。 */
export interface ExampleTemplate {
  id: string;
  labelKey: string;
  definition: EndpointDefinition;
}

// JSON 导入推断出的是宽字符串类型；模板随版分发，后端遍历用例保证它们过共享校验器。
export const EXAMPLE_TEMPLATES: ExampleTemplate[] = [
  {
    id: "generic-submit-poll",
    labelKey: "ce_template_generic_video",
    definition: genericSubmitPoll as unknown as EndpointDefinition,
  },
  {
    id: "generic-image-submit-poll",
    labelKey: "ce_template_generic_image",
    definition: genericImageSubmitPoll as unknown as EndpointDefinition,
  },
];
