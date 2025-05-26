from flask import Flask, redirect, request, session, url_for
import os
import google.auth.transport.requests
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import datetime

app = Flask(__name__)
app.secret_key = os.urandom(24)  # used for session encryption

# OAuth Scopes
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Set up OAuth flow
flow = Flow.from_client_config(
    {
        "web": {
            "client_id": os.environ['GOOGLE_CLIENT_ID'],
            "client_secret": os.environ['GOOGLE_CLIENT_SECRET'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.environ['GOOGLE_REDIRECT_URI']]
        }
    },
    scopes=SCOPES,
    redirect_uri=os.environ['GOOGLE_REDIRECT_URI']
)

@app.route("/authorize")
def authorize():
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route("/oauth2callback")
def oauth2callback():
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    return "✅ Authorization complete! Your AI can now read your calendar."

@app.route("/events")
def events():
    if 'credentials' not in session:
        return redirect(url_for('authorize'))

    creds_data = session['credentials']
    creds = google.oauth2.credentials.Credentials(
        **creds_data
    )

    service = build('calendar', 'v3', credentials=creds)

    now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' = UTC time
    events_result = service.events().list(calendarId='primary', timeMin=now,
                                          maxResults=10, singleEvents=True,
                                          orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        return 'No upcoming events found.'

    output = ""
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        location = event.get('location', 'No location')
        output += f"\n{start}: {event['summary']} — {location}"

    return f"📅 Upcoming Events:{output}"

if __name__ == '__main__':
    app.run(debug=True)
