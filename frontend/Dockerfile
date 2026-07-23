# Frontend image — Next.js standalone build (see next.config.ts's
# `output: 'standalone'`), multi-stage so the final image only carries the
# files actually needed at runtime, not the full node_modules/build cache.

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# API_URL is baked in at build time only for routes Next.js statically
# analyzes at build; the actual proxy routes read it at request time via
# process.env, so this is optional here — set it at `docker run` instead.
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
