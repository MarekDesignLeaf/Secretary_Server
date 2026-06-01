# Google Calendar — Setup Instructions (G3)

Server-side OAuth 2.0 authorization code flow. The Secretary backend holds the
tokens and is the source of truth. You (Marek) perform the Google Cloud Console
steps and grant consent; the backend code is already deployed.

## 1. Google Cloud Console
Open https://console.cloud.google.com/ and sign in with the Google account that
owns the company calendar.

## 2. Create a project
- Top bar -> project selector -> "New Project".
- Name: e.g. "Secretary DesignLeaf".
- Create, then select it.

## 3. Enable the Google Calendar API
- Left menu -> "APIs & Services" -> "Library".
- Search "Google Calendar API" -> open it -> "Enable".

## 4. OAuth consent screen
- "APIs & Services" -> "OAuth consent screen".
- User type: "External" (or "Internal" if using Google Workspace).
- App name: "Secretary". Support email: your email.
- Scopes: add ".../auth/calendar".
- Test users: add the Google account(s) that will connect (while app is in Testing mode).
- Save.

## 5. Create OAuth Client ID (Web application)
- "APIs & Services" -> "Credentials" -> "Create Credentials" -> "OAuth client ID".
- Application type: "Web application".
- Name: "Secretary Backend".

## 6. Authorized redirect URI
Under "Authorized redirect URIs" add EXACTLY:

    https://web-production-4b451.up.railway.app/api/v1/calendar/google/callback

(No trailing slash, must match exactly.) Save.

## 7. Get GOOGLE_CLIENT_ID
After creating, Google shows "Client ID". Copy it.

## 8. Get GOOGLE_CLIENT_SECRET
Same dialog shows "Client secret". Copy it. (Keep it private; never commit it.)

## 9. Set Railway environment variables
In Railway project -> Variables, add:

    GOOGLE_CLIENT_ID       = <the client id from step 7>
    GOOGLE_CLIENT_SECRET   = <the client secret from step 8>
    GOOGLE_REDIRECT_URI    = https://web-production-4b451.up.railway.app/api/v1/calendar/google/callback

Save -> Railway redeploys automatically (~90s).

## How connecting works after setup
1. Android Settings (G4) calls GET /api/v1/calendar/google/connect/start.
2. Backend returns an authorization_url. The app opens it in a browser.
3. You sign in to Google and click "Allow". (This consent is done by you only.)
4. Google redirects to /api/v1/calendar/google/callback; the backend exchanges the
   code for tokens and stores the refresh token in clean_google_calendar_accounts.
5. GET /api/v1/calendar/google/status then shows "connected".

## Security notes
- Tokens are never logged or returned to the app.
- Access tokens auto-refresh via the refresh token.
- If refresh fails, status becomes "needs_reauth" and you reconnect via step 1.
