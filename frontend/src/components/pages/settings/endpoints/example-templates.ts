import type { EndpointDefinition } from "@/types";
import genericImageSubmitPoll from "@/data/example-templates/generic-image-submit-poll.json";
import genericSubmitPoll from "@/data/example-templates/generic-submit-poll.json";

/** Example templates for the new-endpoint form: one submission-and-polling skeleton per media type; selecting one replaces the entire draft. */
export interface ExampleTemplate {
  id: string;
  labelKey: string;
  definition: EndpointDefinition;
}

// JSON imports infer broad string types; templates ship with each release, and backend parameterized cases ensure they pass the shared validator.
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
