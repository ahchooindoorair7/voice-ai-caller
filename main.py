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


def clean_static_folder():
    folder = 'static'
    if os.path.exists(folder):
        for filename in os.listdir(folder):
            filepath = os.path.join(folder, filename)
            try:
                if os.path.isfile(filepath):
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
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.50,
                "similarity_boost": 0.75
            }
        }
    )
    if tts.status_code != 200:
        print("❌ ElevenLabs error:", tts.status_code, tts.text)
        return None
    filename = f"{uuid.uuid4()}.mp3"
    filepath = f"static/{filename}"
    if not os.path.exists("static"):
        os.makedirs("static")
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

    # Context-aware thinking messages
    generic_thinking_lines = [
        "Alright, let me go ahead and check that out for you real quick. Just give me a moment.",
        "Give me just a moment while I look into that for you. It should not take long here. ",
        "Perfect I can definitely help you out here. Give me a second while I go through some things.",
        "Okay, hang tight — I’m just checking a few things.",
    ]
    zip_thinking_lines = [
        "Alright give me a second — I’m pulling up our calendar that way I can cross reference when we will be close to your area to ensure we are there as on time as possible for you.",
    ]
    thinking_lines = zip_thinking_lines if user_zip else generic_thinking_lines
    thinking_text = random.choice(thinking_lines)
    thinking_path = synthesize_speech(thinking_text)

    if not thinking_path:
        return Response("<Response><Say>One moment while I check on that.</Say></Response>", mimetype="application/xml")

    # Kick off GPT response generation in background
    gpt_reply = ""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_gpt = executor.submit(lambda: openai.OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o",
            messages=history,
            stream=True
        ))

        if user_zip:
            try:
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
            except Exception as e:
                print("❌ Calendar access error:", e)

        response = future_gpt.result()
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                gpt_reply += delta

    print(f"🤖 GPT reply: {gpt_reply}")
    history.append({"role": "assistant", "content": gpt_reply})
    save_conversation(sid, history)

    reply_path = synthesize_speech(gpt_reply)
    if not reply_path:
        return Response("<Response><Say>Sorry, there was an error playing the response.</Say></Response>", mimetype="application/xml")

    return Response(f"""
    <Response>
        <Play>https://{request.host}/{thinking_path}</Play>
        <Play>https://{request.host}/{reply_path}</Play>
        <Gather input=\"speech\" action=\"/voice\" method=\"POST\" timeout=\"5\" />
    </Response>
    """, mimetype="application/xml")


@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)







       




