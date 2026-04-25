import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:         '#08090d',
        surface:    '#0e1117',
        card:       '#13161f',
        'card-hi':  '#191d2b',
        border:     '#1e2436',
        'border-hi':'#2c3450',
        tx:         '#e4e8f4',
        muted:      '#6b7590',
        buy:        '#10d98e',
        sell:       '#f04d5a',
        hold:       '#f5a623',
        accent:     '#6c71f0',
      },
      fontFamily: {
        mono: ['var(--font-mono)', 'monospace'],
      },
      animation: {
        'spin-slow': 'spin 0.8s linear infinite',
        'fade-up': 'fadeUp 0.4s ease both',
      },
      keyframes: {
        fadeUp: {
          from: { opacity: '0', transform: 'translateY(14px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
