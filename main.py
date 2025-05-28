import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
import shutil
import random
import threading
import concurrent.futures

from flask import Flask, request, Response, redirect
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL")

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
city_to_zip = {
    "houston": "77002", "sugar land": "77479", "katy": "77494",
    "the woodlands": "77380", "cypress": "77429", "bellaire": "77401", "tomball": "77375"
}

redis_client = redis.from_url(REDIS_URL)

PRELOADED_THINKING_MESSAGES = []
PRELOADED_ZIP_THINKING_MESSAGES = []

# Load pre-generated ElevenLabs thinking MP3s from static folders
PRELOADED_THINKING_MESSAGES_FOLDER = "static/thinking"
PRELOADED_ZIP_THINKING_MESSAGES_FOLDER = "static/thinking_zip"

if os.path.exists(PRELOADED_THINKING_MESSAGES_FOLDER):
    for f in os.listdir(PRELOADED_THINKING_MESSAGES_FOLDER):
        if f.endswith(".mp3"):
            PRELOADED_THINKING_MESSAGES.append(f"static/thinking/{f}")

if os.path.exists(PRELOADED_ZIP_THINKING_MESSAGES_FOLDER):
    for f in os.listdir(PRELOADED_ZIP_THINKING_MESSAGES_FOLDER):
        if f.endswith(".mp3"):
            PRELOADED_ZIP_THINKING_MESSAGES.append(f"static/thinking_zip/{f}")

def clean_static_folder():
    folder = 'static'
    if os.path.exists(folder):
        for filename in os.listdir(folder):
            filepath = os.path.join(folder, filename)
            try:
                if os.path.isfile(filepath) and not filepath.startswith("static/thinking") and not filepath.startswith("static/thinking_zip"):
                    os.remove(filepath)
            except Exception as e:
                print(f"Error deleting file {filename}: {e}")

clean_static_folder()

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
    token_json = os.environ.get("GOOGLE_TOKEN")
    if not token_json:
        print("❌ No GOOGLE_TOKEN environment variable found.")
        return None
    try:
        data = json.loads(token_json)
        return Credentials.from_authorized_user_info(data, SCOPES)
    except Exception as e:
        print("❌ Failed to load credentials from GOOGLE_TOKEN:", e)
        return None

def load_conversation(sid):
    data = redis_client.get(sid)
    return json.loads(data) if data else []

def save_conversation(sid, history):
    if len(history) > 7:
        history = history[:1] + history[-6:]
    redis_client.set(sid, json.dumps(history), ex=3600)

def clear_conversation(sid):
    redis_client.delete(sid)

def synthesize_speech(text):
    tts = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.4
            }
        }
    )
    if tts.status_code != 200:
        print("❌ ElevenLabs error:", tts.status_code, tts.text)
        return None
    filename = f"{uuid.uuid4()}.mp3"
    filepath = f"static/{filename}"
    with open(filepath, "wb") as f:
        f.write(tts.content)
    return filepath

@app.route("/voice", methods=["POST"])
def voice():
    direction = request.form.get("Direction", "").lower()
    sid = request.form.get("CallSid", str(uuid.uuid4()))
    user_input = request.form.get("SpeechResult", "").strip()

    if "goodbye" in user_input.lower():
        clear_conversation(sid)
        return Response("<Response><Say>Okay, goodbye! Thanks for calling.</Say><Hangup/></Response>", mimetype="application/xml")

    history = load_conversation(sid)
    if not history:
        if direction == "inbound":
            intro = "Hey, this is Nick with AhCHOO! Indoor Air Quality Specialists. How can I help you?"
        elif direction == "outbound-api":
            intro = "Hey, this is Nick with AhCHOO! Indoor Air Quality Specialists. You submitted an action form looking to get some information on our air duct cleaning & HVAC sanitation process. What is your zip code so I can make sure we service your area?"
        else:
            intro = "Hi, this is Nick from AhCHOO! Indoor Air Quality Specialists. How can I help you?"
        history.append({"role": "assistant", "content": intro})

    history.insert(0, {
        "role": "system",
        "content": "You are Nick from AH-CHOO! Indoor Air Quality Specialists. Speak friendly and professionally. Ask for ZIP codes, offer calendar availability, and schedule estimates."
    })

    history.append({"role": "user", "content": user_input})
    user_zip = extract_zip_or_city(user_input)

    redis_client.set(f"history:{sid}", json.dumps(history), ex=3600)
    redis_client.set(f"zip:{sid}", user_zip or "", ex=3600)

    if user_zip and PRELOADED_ZIP_THINKING_MESSAGES:
        thinking_path = random.choice(PRELOADED_ZIP_THINKING_MESSAGES)
    elif PRELOADED_THINKING_MESSAGES:
        thinking_path = random.choice(PRELOADED_THINKING_MESSAGES)
    else:
        thinking_path = None

    if not thinking_path:
        return Response("<Response><Say>One moment while I check on that.</Say><Redirect>/response</Redirect></Response>", mimetype="application/xml")

    return Response(f"""
    <Response>
        <Play>https://{request.host}/{thinking_path}</Play>
        <Redirect>/response?sid={sid}</Redirect>
    </Response>
    """, mimetype="application/xml")

@app.route("/response", methods=["POST", "GET"])
def response():
    sid = request.values.get("sid")
    history = json.loads(redis_client.get(f"history:{sid}") or b"[]")
    user_zip = redis_client.get(f"zip:{sid}")
    user_zip = user_zip.decode() if user_zip else None

    gpt_reply = ""
    try:
        if user_zip:
            creds = load_credentials()
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            service = build('calendar', 'v3', credentials=creds)
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            events = service.events().list(calendarId='primary', timeMin=now,
                                           maxResults=10, singleEvents=True,
                                           orderBy='startTime').execute().get('items', [])
            matches = get_calendar_zip_matches(user_zip, events)
            calendar_prompt = build_zip_prompt(user_zip, matches)
            history.append({"role": "assistant", "content": calendar_prompt})

        openai.api_key = OPENAI_API_KEY
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=history,
            stream=True
        )
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                gpt_reply += delta
    except Exception as e:
        print("❌ GPT generation error:", e)
        return Response("<Response><Say>Sorry, there was an error processing your request.</Say></Response>", mimetype="application/xml")

    print(f"🤖 GPT reply: {gpt_reply}")
    history.append({"role": "assistant", "content": gpt_reply})
    save_conversation(sid, history)

    reply_path = synthesize_speech(gpt_reply)
    if not reply_path:
        return Response("<Response><Say>Sorry, there was an error playing the response.</Say></Response>", mimetype="application/xml")

    return Response(f"""
    <Response>
        <Play>https://{request.host}/{reply_path}</Play>
        <Gather input="speech" action="/voice" method="POST" timeout="5" />
    </Response>
    """, mimetype="application/xml")

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
