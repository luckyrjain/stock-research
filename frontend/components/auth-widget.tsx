'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@/lib/auth';

/** Sign-in link when logged out, or an email + dropdown to sign out when
 * logged in. Dropped into every page's nav bar next to HeaderSearch, same
 * rollout pattern as the Watchlist nav link. */
export default function AuthWidget() {
  const { user, loading, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onClickOutside);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  if (loading) return null;

  if (!user) {
    return (
      <Link
        href="/login"
        className="text-xs font-semibold text-muted hover:text-accent transition-colors"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="relative" ref={ref}>
      <button
        ref={triggerRef}
        onClick={() => setOpen(o => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls="auth-widget-menu"
        className="text-xs font-semibold text-muted hover:text-accent transition-colors max-w-[12rem] truncate"
      >
        {user.email}
      </button>
      {open && (
        <div
          id="auth-widget-menu"
          role="menu"
          className="absolute right-0 mt-2 w-44 rounded-lg bg-card border border-border shadow-lg py-1 z-20"
        >
          {/* API Keys now lives in the primary nav (SiteNav) so a
              signed-out visitor can discover it too — no need to duplicate
              the link here as well. */}
          <button
            role="menuitem"
            onClick={() => { setOpen(false); logout(); }}
            className="w-full text-left px-3 py-2 text-xs text-muted hover:text-sell hover:bg-sell/5 transition-colors"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
