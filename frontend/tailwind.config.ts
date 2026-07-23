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
        muted:      '#6b7fa8',
        buy:        '#10d98e',  // Vibrant green — gains
        sell:       '#e05568',  // Muted red — losses (brief: "muted reds")
        hold:       '#f5a623',
        accent:     '#4d7fff',  // True blue — matches Midnight Blue theme
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
  plugins: [],
};

export default config;
