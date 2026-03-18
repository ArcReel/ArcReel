/**
 * Mock for @lobehub/icons and its subpath exports.
 * @lobehub/fluent-emoji (a transitive dependency) uses ESM directory imports
 * that Node cannot resolve in the test environment.
 */
import React from "react";

const NullIcon = () => null;

// Named exports used via `import { Jimeng } from "@lobehub/icons"`
export const Jimeng = NullIcon;

// Default export (covers deep subpath imports like
// `import GeminiColor from "@lobehub/icons/es/Gemini/components/Color"`)
export default NullIcon;
