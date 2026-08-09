'use client';

import { useWatchlist } from '@/lib/watchlist';

interface Props {
  symbol: string;
  company: string;
  exchange: string;
  size?: 'sm' | 'md';
  className?: string;
}

export default function WatchlistButton({ symbol, company, exchange, size = 'md', className = '' }: Props) {
  const { isWatched, toggle } = useWatchlist();
  const watched = isWatched(symbol);
  const textSize = size === 'sm' ? 'text-base' : 'text-xl';

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        toggle({ symbol, company, exchange });
      }}
      aria-pressed={watched}
      aria-label={watched ? `Remove ${symbol} from watchlist` : `Add ${symbol} to watchlist`}
      title={watched ? 'Remove from watchlist' : 'Add to watchlist'}
      className={`leading-none transition-colors duration-150 ${textSize}
        ${size === 'sm' ? 'p-3 -m-3' : ''}
        ${watched ? 'text-hold hover:text-hold/70' : 'text-muted/60 hover:text-hold'}
        ${className}`}
    >
      {watched ? '★' : '☆'}
    </button>
  );
}
