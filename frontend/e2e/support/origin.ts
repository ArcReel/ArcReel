// Screenshot baselines use default preview ports; displayed local addresses match only on that port.

/** Default preview ports, overridden by `E2E_PORT`. */
export const DEFAULT_E2E_PORT = 4173;

/** Whether page addresses use default preview ports. */
export function isDefaultOrigin(url: string) {
  return new URL(url).port === String(DEFAULT_E2E_PORT);
}
