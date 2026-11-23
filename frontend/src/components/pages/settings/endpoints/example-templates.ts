import type { EndpointDefinition } from "@/types";
import genericImageSubmitPoll from "@/data/example-templates/generic-image-submit-poll.json";
import genericSubmitPoll from "@/data/example-templates/generic-submit-poll.json";

export interface ExampleTemplate {
  id: string;
  labelKey: string;
  definition: EndpointDefinition;
}

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
