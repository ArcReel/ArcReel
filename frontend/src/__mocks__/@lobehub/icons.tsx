/**
 * Mock for @lobehub/icons and its subpath exports.
 * @lobehub/fluent-emoji (a transitive dependency) uses ESM directory imports
 * that Node cannot resolve in the test environment.
 *
 * Renders a minimal `<svg>` so tests that probe for the icon's DOM (e.g.
 * `document.querySelector("svg")`) can verify the icon was loaded, while
 * still avoiding the real package's ESM resolution issues.
 */
const StubIcon = ({ size = 24 }: { size?: number }) => (
  <svg data-testid="lobehub-stub-icon" width={size} height={size} />
);

// Named exports used via `import { Jimeng } from "@lobehub/icons"`
export const Jimeng = StubIcon;

// Default export (covers deep subpath imports like
// `import GeminiColor from "@lobehub/icons/es/Gemini/components/Color"`)
export default StubIcon;
