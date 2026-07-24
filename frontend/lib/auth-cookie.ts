// Shared by the Next.js proxy routes under app/api/auth/* — server-only.
// The raw session token lives in this httpOnly cookie on the frontend's own
// origin; api.py never sees it, only the `Authorization: Bearer <token>`
// header these routes forward it as.
export const AUTH_COOKIE_NAME = 'alphapulse_session';
const THIRTY_DAYS_SECONDS = 60 * 60 * 24 * 30;

export function getSessionTokenFromRequest(req: Request): string | null {
  const cookieHeader = req.headers.get('cookie') ?? '';
  const match = cookieHeader.match(new RegExp(`(?:^|;\\s*)${AUTH_COOKIE_NAME}=([^;]+)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export function setSessionCookieHeader(token: string): string {
  const secure = process.env.NODE_ENV === 'production' ? '; Secure' : '';
  return `${AUTH_COOKIE_NAME}=${encodeURIComponent(token)}; HttpOnly; Path=/; Max-Age=${THIRTY_DAYS_SECONDS}; SameSite=Lax${secure}`;
}

export function clearSessionCookieHeader(): string {
  return `${AUTH_COOKIE_NAME}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax`;
}
