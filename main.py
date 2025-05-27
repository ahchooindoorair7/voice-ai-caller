import os
import uuid
import json
import datetime
import requests
import openai
import re

from flask import Flask, request, Response
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

TOKEN_PATH = "token_store.json"
CONVO_PATH = "conversation_store.json"

city_to_zip = {
    "houston": "77002", "sugar land": "77479", "katy": "77494",
    "the woodlands": "77380", "cypress": "77429", "bellaire": "77401", "tomball": "77375"
}
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

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

def load_conversation(sid):
    if not os.path.exists(CONVO_PATH):
        return {}
    with open(CONVO_PATH, 'r') as f:
        data = json.load(f)
    return data.get(sid, [])

def save_conversation(sid, history):
    if not os.path.exists(CONVO_PATH):
        with open(CONVO_PATH, 'w') as f:
            json.dump({}, f)
    with open(CONVO_PATH, 'r') as f:
        all_data = json.load(f)
    all_data[sid] = history
    with open(CONVO_PATH, 'w') as f:
        json.dump(all_data, f)

def clear_conversation(sid):
    if os.path.exists(CONVO_PATH):
        with open(CONVO_PATH, 'r') as f:
            all_data = json.load(f)
        if sid in all_data:
            del all_data[sid]
            with open(CONVO_PATH, 'w') as f:
                json.dump(all_data, f)

@app.route("/voice", methods=["POST"])
def voice():
    user_input = request.form.get("SpeechResult", "").strip()
    sid = request.form.get("CallSid", str(uuid.uuid4()))
    print(f"🗣️ [{sid}] Transcribed: {user_input}", flush=True)

    if "goodbye" in user_input.lower():
        clear_conversation(sid)
        return Response("<Response><Say>Okay, goodbye! Thanks for calling.</Say><Hangup/></Response>", mimetype="application/xml")

    history = load_conversation(sid)

    user_zip = extract_zip_or_city(user_input)
    calendar_reply = ""

    if user_zip:
        try:
            creds = load_credentials()
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(requests.Request())
            service = build('calendar', 'v3', credentials=creds)
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            events = service.events().list(calendarId='primary', timeMin=now,
                                           maxResults=10, singleEvents=True,
                                           orderBy='startTime').execute().get('items', [])
            matches = get_calendar_zip_matches(user_zip, events)
            calendar_reply = build_zip_prompt(user_zip, matches)
        except Exception as e:
            print("❌ Calendar error:", e)
            calendar_reply = "Sorry, I couldn't check our calendar. But I can still help you schedule something."

    system_msg = {
        "role": "system",
        "content": "You are Nick from AH-CHOO! Indoor Air Quality Specialists. You speak in a friendly and professional tone. Your job is to schedule free in-home estimates. Use ZIP codes to check availability. Always remember the user's past answers. End the call if they say goodbye."
    }

    if not history:
        history.append(system_msg)

    if calendar_reply:
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": calendar_reply})
    else:
        history.append({"role": "user", "content": user_input})

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    chat_completion = client.chat.completions.create(
        model="gpt-4o",
        messages=history
    )
    reply = chat_completion.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})
    save_conversation(sid, history)

    tts = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "text": reply,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.4, "similarity_boost": 1.0}
        }
    )

    if tts.status_code != 200:
        print("❌ ElevenLabs error:", tts.status_code, tts.text)
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
    return "Nick AI Voice Agent is running with memory."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)


       




