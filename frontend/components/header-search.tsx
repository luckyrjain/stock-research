'use client';

import { useCallback, useId, useState } from 'react';
import ConsolidatedCard from './consolidated-card';

// Small ticker lookup shared across every page's nav bar — on submit it opens
// ConsolidatedCard, which is a pure aggregation of what the three modes have
// already cached for that symbol. Deliberately lighter than TickerSearch
// (no live NSE/BSE validation, no autosuggest): typos just come back as an
// all-empty card rather than a rejected search.
export default function HeaderSearch() {
  const [value, setValue]   = useState('');
  const [symbol, setSymbol] = useState<string | null>(null);
  const inputId = useId();

  const submit = useCallback(() => {
    const sym = value.trim().toUpperCase().replace(/[^A-Z0-9&-]/g, '');
    if (sym) setSymbol(sym);
  }, [value]);

  return (
    <>
      <form
        onSubmit={e => { e.preventDefault(); submit(); }}
        className="flex items-center"
      >
        <label htmlFor={inputId} className="sr-only">
          Look up a stock across all AlphaPulse modes
        </label>
        <input
          id={inputId}
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder="What does AlphaPulse think about…"
          maxLength={20}
          spellCheck={false}
          className="w-36 sm:w-56 bg-surface border border-border rounded-lg
                     px-3 py-1.5 text-xs font-mono uppercase tracking-wide
                     text-tx placeholder:text-muted/60 placeholder:normal-case placeholder:font-sans placeholder:tracking-normal
                     outline-none focus:border-accent transition-colors"
        />
      </form>
      {symbol && <ConsolidatedCard symbol={symbol} onClose={() => setSymbol(null)} />}
    </>
  );
}
