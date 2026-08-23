'use client';

import { useEffect, type RefObject } from 'react';

const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface FocusTrapOptions {
  onEscape?: () => void;
  initialFocusRef?: RefObject<HTMLElement | null>;
  /**
   * id of the landmark to make `inert` while the trap is active (A11Y-11:
   * background can't be Tab'd or screen-read into while a dialog covers it).
   * Defaults to 'app-content' (see app/layout.tsx). Automatically skipped if
   * that element turns out to contain the panel itself — true for inline,
   * non-portaled popovers — since inerting an ancestor of the panel would
   * inert the panel too.
   */
  inertTargetId?: string;
}

// Extracted from ConsolidatedCard's original inline implementation — inert
// background + Tab-wrap focus trap + initial focus + Escape-to-close, shared
// by every dialog-shaped surface in this codebase (modals and click-to-open
// popovers with interactive content alike). `active` gates all of it, so
// components whose panel is conditionally rendered in place (rather than the
// whole component being mounted/unmounted) can call this unconditionally
// and pass their own `open` state through.
export function useFocusTrap(
  panelRef: RefObject<HTMLElement | null>,
  active: boolean,
  opts: FocusTrapOptions = {},
) {
  const { onEscape, initialFocusRef, inertTargetId = 'app-content' } = opts;

  useEffect(() => {
    if (!active) return;
    const target = document.getElementById(inertTargetId);
    if (!target || (panelRef.current && target.contains(panelRef.current))) return;
    target.setAttribute('inert', '');
    return () => target.removeAttribute('inert');
  }, [active, inertTargetId, panelRef]);

  useEffect(() => {
    if (!active) return;
    (initialFocusRef?.current ?? panelRef.current)?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onEscape?.(); return; }
      if (e.key !== 'Tab' || !panelRef.current) return;
      const focusables = panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [active, onEscape, initialFocusRef, panelRef]);
}
