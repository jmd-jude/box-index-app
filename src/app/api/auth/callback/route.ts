import { NextRequest, NextResponse } from 'next/server';
import { getIronSession } from 'iron-session';
import { cookies } from 'next/headers';
import { sessionOptions, SessionData } from '@/lib/session';
import { getBoxUser } from '@/lib/box';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get('code');

  const appOrigin = new URL(process.env.BOX_REDIRECT_URI!).origin;

  if (!code) {
    return NextResponse.redirect(new URL('/?error=no_code', appOrigin));
  }

  // Exchange code for tokens
  const params = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    client_id: process.env.BOX_CLIENT_ID!,
    client_secret: process.env.BOX_CLIENT_SECRET!,
    redirect_uri: process.env.BOX_REDIRECT_URI!,
  });

  const tokenRes = await fetch('https://api.box.com/oauth2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });

  if (!tokenRes.ok) {
    return NextResponse.redirect(new URL('/?error=token_exchange', appOrigin));
  }

  const tokenData = await tokenRes.json();

  // Fetch user info
  let user = { name: 'Unknown', login: '' };
  try {
    user = await getBoxUser(tokenData.access_token);
  } catch {
    // non-fatal
  }

  const session = await getIronSession<SessionData>(cookies(), sessionOptions);
  session.accessToken = tokenData.access_token;
  session.refreshToken = tokenData.refresh_token;
  session.expiresAt = Date.now() + tokenData.expires_in * 1000;
  session.userName = user.name;
  session.userEmail = user.login;
  await session.save();

  return NextResponse.redirect(new URL('/', appOrigin));
}
