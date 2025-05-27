from flask import Flask, request, Response, redirect, session, url_for
from flask_session import Session
import openai
import os
import uuid
import requests
import datetime
import google.auth.transport.requests
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import google.oauth2.credentials

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Keys and config
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = "sk_28e9af6168313208486554115e37bd8f06db2c2bf68f75f2"
ELEVENLABS_VOICE_ID = "a73940a2-08f2-4db8-9ebc-63f01eacbe89"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Google Auth Flow
flow = Flow.from_client_config(
    {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI]
        }
    },
    scopes=SCOPES,
    redirect_uri=GOOGLE_REDIRECT_URI
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
    creds = google.oauth2.credentials.Credentials(**creds_data)
    service = build('calendar', 'v3', credentials=creds)
    now = datetime.datetime.utcnow().isoformat() + 'Z'
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

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

@app.route("/voice", methods=["POST"])
def voice():
    print("🟢 Twilio POST /voice received", flush=True)
    prompt = "You are Nick from AH-CHOO! Indoor Air Quality Specialists. Greet the caller and offer a free estimate."
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        print("📡 Sending prompt to GPT-4o...", flush=True)
        chat_completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a friendly, upbeat sales rep."},
                {"role": "user", "content": prompt}
            ]
        )
        reply = chat_completion.choices[0].message.content.strip()
        print("✅ GPT-4o replied:", reply, flush=True)
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "text": reply,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.4,
                    "similarity_boost": 1.0
                }
            }
        )
        if response.status_code != 200:
            print("❌ ElevenLabs Error:", response.text, flush=True)
            raise Exception("Failed to generate audio")
        filename = f"{uuid.uuid4()}.mp3"
        filepath = f"static/{filename}"
        if not os.path.exists("static"):
            os.makedirs("static")
        with open(filepath, "wb") as f:
            f.write(response.content)
        print("🎧 Audio saved to:", filepath, flush=True)
        return Response(f"""
        <Response>
            <Play>https://{request.host}/static/{filename}</Play>
        </Response>
        """, mimetype="application/xml")
    except Exception as e:
        print("❌ ERROR:", e, flush=True)
        return Response("""
        <Response>
            <Say>Sorry, something went wrong. We'll call you back shortly.</Say>
        </Response>
        """, mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)


