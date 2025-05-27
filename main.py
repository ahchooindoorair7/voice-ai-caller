import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask import Flask, request, Response
from flask_session import Session
import uuid
import requests
import openai
import datetime
import re
from googleapiclient.discovery import build
import google.oauth2.credentials

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")

city_to_zip = {
    "houston": "77002",
    "sugar land": "77479",
    "katy": "77494",
    "the woodlands": "77380",
    "cypress": "77429",
    "bellaire": "77401",
    "tomball": "77375"
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
        if match:
            calendar_zip = match.group(0)
            if calendar_zip == user_zip:
                start = event['start'].get('dateTime', event['start'].get('date'))
                matches.append(start)
    return matches

def build_zip_prompt(user_zip, matches):
    if matches:
        time_list = ', '.join(matches[:2])
        return f"We’ll already be in your area ({user_zip}) at {time_list}. Would one of those work for a free estimate?"
    else:
        return f"We’re not currently scheduled in {user_zip}, but I can open up time for you. What day works best?"

@app.route("/voice", methods=["POST"])
def voice():
    # 👂 Get transcription from Twilio webhook
    user_input = request.form.get('SpeechResult', '') or request.form.get('TranscriptionText', '')

    print(f"📥 Incoming speech: {user_input}", flush=True)
    user_zip = extract_zip_or_city(user_input)

    creds_data = {
        'token': os.environ.get('GOOGLE_TOKEN'),
        'refresh_token': None,
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': os.environ.get('GOOGLE_CLIENT_ID'),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'scopes': ['https://www.googleapis.com/auth/calendar.readonly']
    }

    try:
        creds = google.oauth2.credentials.Credentials(**creds_data)
        service = build('calendar', 'v3', credentials=creds)
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = service.events().list(calendarId='primary', timeMin=now,
                                       maxResults=10, singleEvents=True,
                                       orderBy='startTime').execute().get('items', [])
        matches = get_calendar_zip_matches(user_zip, events)
        response_text = build_zip_prompt(user_zip, matches)
    except Exception as e:
        print("❌ Calendar error:", e, flush=True)
        response_text = "Sorry, I couldn't access the calendar right now. Can I still help you schedule a free estimate?"

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    chat_completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are Nick from AH-CHOO! Indoor Air Quality Specialists. Your goal is to sound friendly and helpful. Ask the caller for their ZIP code so you can check your calendar and book a free estimate nearby."},
            {"role": "user", "content": response_text}
        ]
    )
    reply = chat_completion.choices[0].message.content.strip()

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
       




