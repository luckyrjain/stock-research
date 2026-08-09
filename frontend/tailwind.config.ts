import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:         '#0b1120',  // Midnight Blue foundation (AlphaPulse brief)
        surface:    '#0f1829',
        card:       '#132040',
        'card-hi':  '#1a2848',
        border:     '#1d2e4e',
        'border-hi':'#243860',
        tx:         '#e2e8f4',
        // Was #6b7fa8, which measured 3.99:1 on `card` — below the 4.5:1 WCAG AA
        // body-text bar, and this is MetricRow's label, the most-repeated text
        // pairing in the app. #8093bd measures 5.22:1 on `card` and 6.12:1 on
        // `bg`. Do not darken without re-measuring both.
        muted:      '#8093bd',
        buy:        '#10d98e',  // Vibrant green — gains
        // COLOR-04 (design.md): lightened in Revision 2, hue/saturation held,
        // only HSL lightness moved (+5pt), so every token clears AA on every
        // surface. Was #e05568.
        sell:       '#e46b7b',
        hold:       '#f5a623',
        // COLOR-04 (design.md): lightened in Revision 2 (+4pt lightness, hue/
        // saturation held) alongside `sell`, same AA-everywhere reasoning.
        // Was #4d7fff.
        accent:     '#618eff',
      },
      boxShadow: {
        // Accent glow for the primary CTA. Tokenised because a Tailwind
        // arbitrary value can't read `theme.colors.accent` — the one previous
        // inline use hardcoded #6c71f0, a retired accent, and silently rotted
        // when the palette moved off it.
        //
        // Named `accent-glow`, NOT `accent`: a boxShadow key that collides with
        // a colors key makes Tailwind emit `.shadow-accent` twice — once as the
        // shadow and once as a `boxShadowColor` utility setting
        // `--tw-shadow-color`. The second wins, dropping the `40` alpha, so the
        // glow rendered at full opacity at rest and *dimmed* on hover (37.6%),
        // inverting the interaction. `-lg` escaped this only because it isn't
        // also a color name.
        'accent-glow':    '0 4px 24px #618eff40',
        'accent-glow-lg': '0 6px 28px #618eff60',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'monospace'],
      },
      animation: {
        'spin-slow': 'spin 0.8s linear infinite',
        'fade-up':   'fadeUp 0.4s ease both',
        'shimmer':   'shimmer 1.8s linear infinite',
      },
      keyframes: {
        fadeUp: {
          from: { opacity: '0', transform: 'translateY(14px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
    },
  },
  // @tailwindcss/container-queries, admitted under PAGE-04 (design.md) so
  // ResultsDashboard's internal grids can reflow on their own column width
  // rather than the window's — the prerequisite for lifting /compare's
  // two-symbol cap. A second plugin requires an amendment (META-01).
  plugins: [require('@tailwindcss/container-queries')],
};

export default config;
