'use client';

import { useState, useRef, useCallback, useId } from 'react';
import type { ValidationResult } from '@/types';

interface Props {
  onAnalyse: (symbol: string) => void;
  disabled: boolean;
  compact?: boolean;
}

export default function TickerSearch({ onAnalyse, disabled, compact = false }: Props) {
  const [value, setValue]           = useState('');
  const [status, setStatus]         = useState<'idle' | 'loading' | 'valid' | 'invalid' | 'warn'>('idle');
  const [result, setResult]         = useState<ValidationResult | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const validSymbol = useRef<string | null>(null);
  const suggestionsId = useId();

  const validate = useCallback(async (sym: string, exchange?: string) => {
    if (!sym) { setStatus('idle'); setResult(null); validSymbol.current = null; return; }
    setStatus('loading');
    try {
      const url = exchange ? `/api/validate/${sym}?exchange=${exchange}` : `/api/validate/${sym}`;
      const res  = await fetch(url);
      const data: ValidationResult = await res.json();
      setResult(data);
      if (data.valid) {
        setStatus('valid');
        validSymbol.current = data.symbol;
      } else if (data.found && data.suspended) {
        setStatus('warn');
        validSymbol.current = null;
      } else {
        setStatus('invalid');
        validSymbol.current = null;
      }
    } catch {
      setStatus('idle');
    }
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value.toUpperCase().replace(/[^A-Z0-9&]/g, '');
    setValue(v);
    setStatus('idle');
    setResult(null);
    validSymbol.current = null;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (v.length >= 1) debounceRef.current = setTimeout(() => validate(v), 420);
  };

  const handleAnalyse = () => {
    if (validSymbol.current) onAnalyse(validSymbol.current);
  };

  const selectSuggestion = (sym: string, exchange?: string) => {
    setValue(sym);
    validate(sym, exchange);
  };

  const borderColor =
    status === 'valid'   ? 'border-buy   focus:border-buy   shadow-buy/10'   :
    status === 'invalid' ? 'border-sell  focus:border-sell  shadow-sell/10'  :
    status === 'warn'    ? 'border-hold  focus:border-hold  shadow-hold/10'  :
    'border-border focus:border-accent shadow-accent/10';

  const statusLabel =
    status === 'loading' ? 'Checking symbol…' :
    status === 'valid'   ? 'Symbol found' :
    status === 'invalid' ? 'Symbol not found' :
    status === 'warn'    ? 'Symbol suspended or delisted' : '';

  const statusIcon =
    status === 'loading' ? <span aria-hidden="true" className="animate-spin-slow inline-block text-muted">⟳</span> :
    status === 'valid'   ? <span aria-hidden="true" className="text-buy">✓</span> :
    status === 'invalid' ? <span aria-hidden="true" className="text-sell">✕</span> :
    status === 'warn'    ? <span aria-hidden="true" className="text-hold">⚠</span> : null;

  return (
    <div className={`flex flex-col items-center gap-4 ${compact ? 'mb-6' : 'mb-12'}`}>
      {!compact && (
        <p className="text-muted text-sm tracking-wide">
          Enter an NSE or BSE stock ticker to begin your research
        </p>
      )}

      <div className="w-full max-w-lg flex flex-col items-center gap-3">
        {/* Input */}
        <div className="relative w-full">
          <input
            value={value}
            onChange={handleChange}
            onKeyDown={e => e.key === 'Enter' && validSymbol.current && !disabled && handleAnalyse()}
            disabled={disabled}
            placeholder="e.g. TCS, RELIANCE, INFY"
            maxLength={20}
            spellCheck={false}
            aria-label="NSE or BSE stock ticker"
            aria-describedby={suggestionsId}
            className={`w-full pr-12 bg-card border-2 rounded-xl
              font-mono font-bold tracking-[2px] uppercase
              text-tx placeholder:text-muted/50 placeholder:font-normal placeholder:tracking-normal
              outline-none transition-all duration-200
              focus:shadow-[0_0_0_4px]
              disabled:opacity-40 disabled:cursor-not-allowed
              ${compact ? 'px-4 py-3 text-base' : 'px-6 py-5 text-[22px]'}
              ${borderColor}`}
          />
          <span className="absolute right-5 top-1/2 -translate-y-1/2 text-lg">
            {statusIcon}
          </span>
          {/* Visually hidden — announces validation status changes to screen readers,
              which the icon-only indicator above doesn't. */}
          <span id={suggestionsId} className="sr-only" aria-live="polite">{statusLabel}</span>
        </div>

        {/* Company row */}
        {status === 'valid' && result && (
          <div className="flex items-center gap-2 w-full">
            <span className="text-sm font-medium text-tx">{result.company}</span>
            <span className={`text-[11px] font-mono font-semibold px-2 py-0.5 rounded border
              ${result.exchange === 'BSE'
                ? 'bg-hold/10 text-hold border-hold/20'
                : 'bg-buy/10 text-buy border-buy/20'}`}>
              {result.exchange ?? 'NSE'}
            </span>
          </div>
        )}
        {status === 'warn' && result && (
          <p className="text-sm text-hold w-full">
            ⚠ {result.company} — suspended or delisted on NSE
          </p>
        )}
        {status === 'invalid' && (
          <p className="text-sm text-muted w-full">Symbol not found on NSE / BSE</p>
        )}

        {/* Suggestions — shown on invalid/warn AND as "also try" alternatives when valid */}
        {result?.suggestions && result.suggestions.length > 0 && (
          <div className="w-full bg-card-hi border border-border-hi rounded-lg overflow-hidden">
            {status === 'valid' && (
              <p className="px-4 pt-2.5 pb-1 text-[11px] text-muted uppercase tracking-wide font-semibold">
                Also try
              </p>
            )}
            {result.suggestions.map(s => (
              <button
                key={`${s.symbol}-${s.exchange}`}
                onClick={() => selectSuggestion(s.symbol, s.exchange)}
                className="w-full flex items-center justify-between px-4 py-2.5
                  text-sm hover:bg-border transition-colors text-left"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold text-accent">{s.symbol}</span>
                  {s.exchange && (
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border
                      ${s.exchange === 'BSE'
                        ? 'bg-hold/10 text-hold border-hold/20'
                        : 'bg-buy/10 text-buy border-buy/20'}`}>
                      {s.exchange}
                    </span>
                  )}
                </div>
                <span className="text-muted text-xs truncate max-w-[180px]">{s.company}</span>
              </button>
            ))}
          </div>
        )}

        {/* CTA */}
        <button
          onClick={handleAnalyse}
          disabled={status !== 'valid' || disabled}
          className={`rounded-xl font-semibold tracking-wide
            bg-accent text-bg shadow-accent-glow
            hover:opacity-90 hover:shadow-accent-glow-lg
            active:scale-[.98] transition-all duration-150
            disabled:opacity-30 disabled:cursor-not-allowed disabled:shadow-none
            ${compact ? 'mt-1 px-7 py-2.5 text-sm' : 'mt-2 px-10 py-3.5 text-[15px]'}`}
        >
          {disabled ? 'Running…' : 'Analyse Stock'}
        </button>
      </div>
    </div>
  );
}
