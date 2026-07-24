'use client';

import { useEffect } from 'react';

/** Registers public/sw.js so the app is installable and previously-visited
 * pages/static assets work offline. Renders nothing.
 *
 * Production-only: registering in `next dev` would install a real,
 * persisted service worker in every engineer's browser profile that then
 * cache-first-intercepts static assets across future dev sessions — the
 * classic "my code changes aren't showing up, gotta unregister the SW" trap. */
export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') return;
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // installability/offline support is a progressive enhancement — a
      // registration failure shouldn't be user-visible
    });
  }, []);

  return null;
}
