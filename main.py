import os
import uuid
import datetime
import requests
import openai
import re
import json

from flask import Flask, request, Response, redirect
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

TOKEN_PATH = "token_store.json"

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
city_to_zip = {
    "houston": "77002", "sugar land": "77479", "katy": "77494",
    "the woodlands": "77380", "cypress": "77429", "bellaire": "77401", "tomball": "77375"
}

def extract_zip_or_city(text):
    zip_match = re.search(r'\b77\d{3}\b', text)
    if zip_match:
        return zip_match.group(0)
    for city in city_to_zip:
        if city in text.lower():
            return city_to_zip[city]
    return None

def get_calendar_zip_matches(user_zip, events):
    matches = []
    for event in events:
        location = event.get('location', '')
        match = re.search(r'\b77\d{3}\b', location)
        if match and match.group(0) == user_zip:
            start = event['start'].get('dateTime', event['start'].get('date'))
            matches.append(start)
    return matches

def build_zip_prompt(user_zip, matches):
    if matches:
        return f"We’ll already be in your area ({user_zip}) at {', '.join(matches[:2])}. Would one of those work for a free estimate?"
    else:
        return f"We’re not currently scheduled in {user_zip}, but I can open up time for you. What day works best?"

def load_credentials():
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH, 'r') as token_file:
        data = json.load(token_file)
    return Credentials.from_authorized_user_info(data, SCOPES)

def save_credentials(creds):
    with open(TOKEN_PATH, 'w') as token_file:
        token_file.write(creds.to_json())

@app.route("/authorize")
def authorize():
    flow = Flow.from_client_config(
        {
            "installed": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES,
        redirect_uri="https://voice-ai-caller.onrender.com/oauth2callback"
    )
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    flow = Flow.from_client_config(
        {
            "installed": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES,
        redirect_uri="https://voice-ai-caller.onrender.com/oauth2callback"
    )
    flow.fetch_token(authorization_response=request.url)
    save_credentials(flow.credentials)
    return "✅ Authorization complete! You can close this window."

@app.route("/voice", methods=["POST"])
def voice():
    user_input = request.form.get("SpeechResult", "").strip()
    print(f"🗣️ Transcribed: {user_input}", flush=True)

    if "goodbye" in user_input.lower():
        return Response("<Response><Say>Okay, goodbye!</Say><Hangup/></Response>", mimetype="application/xml")

    user_zip = extract_zip_or_city(user_input)

    try:
        creds = load_credentials()
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(requests.Request())
                save_credentials(creds)
            else:
                raise Exception("❌ Calendar access not available or not authorized.")

        service = build('calendar', 'v3', credentials=creds)
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = service.events().list(calendarId='primary', timeMin=now,
                                       maxResults=10, singleEvents=True,
                                       orderBy='startTime').execute().get('items', [])
        response_text = build_zip_prompt(user_zip, get_calendar_zip_matches(user_zip, events))
    except Exception as e:
        print("❌ Calendar error:", e, flush=True)
        response_text = "Sorry, I couldn't access the calendar right now. Can I still help you schedule a free estimate?"

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    chat_completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are Nick from AH-CHOO! Indoor Air Quality Specialists. Ask for the ZIP code and schedule a free estimate. End the call if the user says goodbye."},
            {"role": "user", "content": user_input or "start"}
        ]
    )
    reply = chat_completion.choices[0].message.content.strip()

    tts = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "text": reply,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": { "stability": 0.4, "similarity_boost": 1.0 }
        }
    )

    if tts.status_code != 200:
        print("❌ ElevenLabs error:", tts.status_code, tts.text, flush=True)
        return Response("<Response><Say>Sorry, something went wrong.</Say></Response>", mimetype="application/xml")

    filename = f"{uuid.uuid4()}.mp3"
    filepath = f"static/{filename}"
    if not os.path.exists("static"):
        os.makedirs("static")
    with open(filepath, "wb") as f:
        f.write(tts.content)

    return Response(f"""
    <Response>
        <Play>https://{request.host}/static/{filename}</Play>
        <Gather input="speech" action="/voice" method="POST" timeout="5" />
    </Response>
    """, mimetype="application/xml")

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)

       




