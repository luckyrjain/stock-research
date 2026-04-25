'use client';

import { useState, useRef, useCallback } from 'react';
import type { ValidationResult } from '@/types';

interface Props {
  onAnalyse: (symbol: string) => void;
  disabled: boolean;
}

export default function TickerSearch({ onAnalyse, disabled }: Props) {
  const [value, setValue]           = useState('');
  const [status, setStatus]         = useState<'idle' | 'loading' | 'valid' | 'invalid' | 'warn'>('idle');
  const [result, setResult]         = useState<ValidationResult | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const validSymbol = useRef<string | null>(null);

  const validate = useCallback(async (sym: string) => {
    if (!sym) { setStatus('idle'); setResult(null); validSymbol.current = null; return; }
    setStatus('loading');
    try {
      const res  = await fetch(`/api/validate/${sym}`);
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

  const selectSuggestion = (sym: string) => {
    setValue(sym);
    validate(sym);
  };

  const borderColor =
    status === 'valid'   ? 'border-buy   focus:border-buy   shadow-buy/10'   :
    status === 'invalid' ? 'border-sell  focus:border-sell  shadow-sell/10'  :
    status === 'warn'    ? 'border-hold  focus:border-hold  shadow-hold/10'  :
    'border-border focus:border-accent shadow-accent/10';

  const statusIcon =
    status === 'loading' ? <span className="animate-spin-slow inline-block text-muted">⟳</span> :
    status === 'valid'   ? <span className="text-buy">✓</span> :
    status === 'invalid' ? <span className="text-sell">✕</span> :
    status === 'warn'    ? <span className="text-hold">⚠</span> : null;

  return (
    <div className="flex flex-col items-center gap-6 mb-12">
      <p className="text-muted text-sm tracking-wide">
        Enter an NSE or BSE stock ticker to begin your research
      </p>

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
            className={`w-full px-6 py-5 pr-14 bg-card border-2 rounded-xl
              font-mono text-[22px] font-bold tracking-[2px] uppercase
              text-tx placeholder:text-muted placeholder:font-normal placeholder:tracking-normal
              outline-none transition-all duration-200
              focus:shadow-[0_0_0_4px]
              disabled:opacity-40 disabled:cursor-not-allowed
              ${borderColor}`}
          />
          <span className="absolute right-5 top-1/2 -translate-y-1/2 text-lg">
            {statusIcon}
          </span>
        </div>

        {/* Company row */}
        {status === 'valid' && result && (
          <div className="flex items-center gap-2 w-full">
            <span className="text-sm font-medium text-tx">{result.company}</span>
            <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-buy/10 text-buy border border-buy/20">
              NSE
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

        {/* Suggestions */}
        {result?.suggestions && result.suggestions.length > 0 && status !== 'valid' && (
          <div className="w-full bg-card-hi border border-border-hi rounded-lg overflow-hidden">
            {result.suggestions.map(s => (
              <button
                key={s.symbol}
                onClick={() => selectSuggestion(s.symbol)}
                className="w-full flex items-center justify-between px-4 py-2.5
                  text-sm hover:bg-border transition-colors text-left"
              >
                <span className="font-mono font-semibold text-accent">{s.symbol}</span>
                <span className="text-muted text-xs">{s.company}</span>
              </button>
            ))}
          </div>
        )}

        {/* CTA */}
        <button
          onClick={handleAnalyse}
          disabled={status !== 'valid' || disabled}
          className="mt-2 px-10 py-3.5 rounded-xl font-semibold text-[15px] tracking-wide
            bg-accent text-white shadow-[0_4px_24px_#6c71f040]
            hover:opacity-90 hover:shadow-[0_6px_28px_#6c71f060]
            active:scale-[.98] transition-all duration-150
            disabled:opacity-30 disabled:cursor-not-allowed disabled:shadow-none"
        >
          {disabled ? 'Running…' : 'Analyse Stock'}
        </button>
      </div>
    </div>
  );
}
