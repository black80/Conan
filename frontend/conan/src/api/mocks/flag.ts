/**
 * Build-time switch for the zero-backend demo. Set by .env.production so the default
 * `npm run build` bakes in mocked transports; `npm run dev` leaves it unset and talks
 * to the real python_simulation backend.
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true"
