'use client';

import { usePositions } from '@/lib/positions';

interface Props {
  symbol: string;
  company: string;
  exchange: string;
  entry_price: number | null;
  target_price: number | null;
  stop_loss: number | null;
  size?: 'sm' | 'md';
  className?: string;
}

export default function PositionButton({
  symbol, company, exchange, entry_price, target_price, stop_loss, size = 'md', className = '',
}: Props) {
  const { isPositioned, addPosition, removePosition } = usePositions();
  const bought = isPositioned(symbol);
  const sizeCls = size === 'sm' ? 'text-[10px] px-2 py-0.5' : 'text-xs px-2.5 py-1';

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        if (bought) removePosition(symbol);
        else addPosition({ symbol, company, exchange, entry_price, target_price, stop_loss });
      }}
      aria-pressed={bought}
      aria-label={bought ? `Remove ${symbol} from your tracked positions` : `Mark ${symbol} as bought — track live P&L`}
      title={bought ? 'Remove from your positions' : 'Mark as bought — track live P&L against entry/target/stop'}
      className={`inline-flex items-center gap-1 rounded-full font-bold border transition-colors whitespace-nowrap ${sizeCls}
        ${bought
          ? 'bg-buy/12 text-buy border-buy/25 hover:bg-buy/20'
          : 'bg-transparent border-border text-muted hover:text-tx hover:border-border-hi'}
        ${className}`}
    >
      {bought ? 'Bought ✓' : 'I bought this'}
    </button>
  );
}
