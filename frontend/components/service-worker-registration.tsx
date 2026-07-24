'use client';

import { useEffect } from 'react';

/** Registers public/sw.js so the app is installable and previously-visited
 * pages/static assets work offline. Renders nothing. */
export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // installability/offline support is a progressive enhancement — a
      // registration failure shouldn't be user-visible
    });
  }, []);

  return null;
}
