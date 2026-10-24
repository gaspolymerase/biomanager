# Google Calendar integration — admin setup

This is a one-time setup so users can click "Connect Google Calendar" in
BioManager's calendar page.

It shows Google Calendar inside BioManager, read-only. The other way, putting
BioManager's calendar into Google Calendar, needs none of this: a lab server
open to the internet offers each person a private link (**On your phone**),
and the desktop app on a Mac writes it into the Mac's own Calendar, which
passes it on to Google (**Mac Calendar**, `app/mac_calendar.py`).

## 1. Create a Google Cloud OAuth client

1. Visit [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. Create a project (or pick an existing one).
3. Enable the **Google Calendar API** for that project:
   APIs & Services → Library → search "Google Calendar API" → Enable.
4. APIs & Services → **OAuth consent screen**:
   - Choose "External" if you have a Google account (not a Workspace).
   - App name: `BioManager`. User support email: yours.
   - Scopes: add `auth/calendar.readonly` and `auth/userinfo.email`.
   - Test users: add the Google accounts you want to allow during development.
5. APIs & Services → Credentials → **Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized redirect URIs: add
     `http://localhost:5000/calendar/google/callback` (dev)
     and your production URL like `https://your.host/calendar/google/callback`.
6. Copy the **Client ID** and **Client secret**.

## 2. Tell BioManager about the credentials

Add to your `.env` (or whatever you use to set env vars before launching
the Flask app):

```
GOOGLE_OAUTH_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-…
```

If you're running on plain HTTP locally, also set:

```
OAUTHLIB_INSECURE_TRANSPORT=1
```

(Google requires HTTPS for the redirect URI in production; this env var
relaxes that check for development only. Never set it in prod.)

Restart the server.

## 3. Connect from inside BioManager

1. Open `/calendar`.
2. Click **Connected calendars** at the bottom of the calendar's sidebar.
3. Under "Google Calendar", click **Connect Google Calendar**.
4. Approve the consent screen → you're redirected back to the calendar.
5. Your primary Google Calendar events appear in red. Toggle them off
   with the "Subscriptions" filter checkbox or disconnect from the
   Calendars panel.

## Notes

- Only **read** access is requested (`calendar.readonly`). BioManager does
  not push events back to Google.
- The refresh token is stored in the `google_calendar_links` table. To
  force re-authorization (e.g. if a user revokes access at Google), they
  can click **Disconnect** and **Connect** again.
- Events are pulled lazily on every `/calendar/events.json` request,
  which fires when the user navigates the calendar. There's no
  background polling.
