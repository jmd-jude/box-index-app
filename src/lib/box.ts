import { SessionData } from './session';

const TOKEN_URL = 'https://api.box.com/oauth2/token';

export interface BoxUser {
  id: string;
  name: string;
  login: string;
}

/**
 * Returns a valid access token, refreshing if within 5 minutes of expiry.
 * Mutates session in place — caller must save the session after.
 */
export async function getFreshToken(session: SessionData): Promise<string> {
  if (!session.accessToken) throw new Error('Not authenticated');

  const expiresAt = session.expiresAt ?? 0;
  const fiveMinutes = 5 * 60 * 1000;

  if (Date.now() < expiresAt - fiveMinutes) {
    return session.accessToken;
  }

  // Refresh
  if (!session.refreshToken) throw new Error('No refresh token — re-authenticate');

  const params = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: session.refreshToken,
    client_id: process.env.BOX_CLIENT_ID!,
    client_secret: process.env.BOX_CLIENT_SECRET!,
  });

  const res = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });

  if (!res.ok) throw new Error('Token refresh failed');

  const data = await res.json();
  session.accessToken = data.access_token;
  session.refreshToken = data.refresh_token;
  session.expiresAt = Date.now() + data.expires_in * 1000;

  return session.accessToken!;
}

export async function getBoxUser(accessToken: string): Promise<BoxUser> {
  const res = await fetch('https://api.box.com/2.0/users/me', {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error('Failed to fetch user');
  return res.json();
}

export async function uploadToBox(
  accessToken: string,
  folderId: string,
  fileName: string,
  fileBuffer: Buffer
): Promise<string> {
  const form = new FormData();
  form.append(
    'attributes',
    JSON.stringify({ name: fileName, parent: { id: folderId } })
  );
  form.append('file', new Blob([new Uint8Array(fileBuffer)]), fileName);

  const res = await fetch('https://upload.box.com/api/2.0/files/content', {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}` },
    body: form,
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Box upload failed: HTTP ${res.status} — ${body || '(empty body)'}`);
  }

  const data = await res.json();
  const fileId: string = data.entries[0].id;
  return `https://app.box.com/file/${fileId}`;
}
