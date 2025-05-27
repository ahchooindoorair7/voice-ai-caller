import os
import uuid
import json
import datetime
import requests
import openai
import redis
import re

from flask import Flask, request, Response
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Environment setup
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

REDIS_HOST = os.environ.get("REDIS_HOST")
REDIS_PORT = os.environ.get("REDIS_PORT")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")

redis_client = redis.StrictRedis(
    host=REDIS_HOST,
    port=int(REDIS_PORT),
    password=REDIS_PASSWORD,
    decode_responses=True
)

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

city_to_zip = {
    "houston": "77002", "sugar land": "77479", "katy": "77494",
    "the woodlands": "77380", "cypress": "77429", "bellaire": "77401", "tomball": "77375"
}

FAQ_RESPONSES = {
    "price": "So as you saw in the ad video we clean the full hvac system. Since that is the case, we do free in-home estimates so we can see exactly what needs to be done. Then we know what our time & materials will be, so we could give you a price. We don't like to play the whole add-on upcharge game. Whatever price we give you will stay there. Would you rather an afternoon, or night appointment? We do estimates until 9pm!",
    "cost": "Most homes fall in the $399 to $799 range depending on size and number of systems.",
    "what do you do": "We clean air ducts, sanitize HVAC systems, and improve your indoor air quality.",
    "services": "We specialize in whole-home air duct cleaning and HVAC sanitation.",
    "how long": "Most jobs take between 1.5 to 3 hours depending on your home's size.",
    "schedule": "I’d be happy to help schedule your estimate! What's your ZIP code so I can check availability?",
    "hours": "We typically schedule between 8 AM and 6 PM Monday through Saturday."
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

def load_conversation(sid):
    raw = redis_client.get(sid)
    return json.loads(raw) if raw else []

def save_conversation(sid, history):
    redis_client.setex(sid, 3600, json.dumps(history))  # expires in 1 hour

def clear_conversation(sid):
    redis_client.delete(sid)

def load_credentials():
    try:
        data = json.loads(GOOGLE_TOKEN)
        return Credentials.from_authorized_user_info(data, SCOPES)
    except Exception as e:
        print("❌ Failed to load Google credentials:", e)
        return None

@app.route("/voice", methods=["POST"])
def voice():
    sid = request.form.get("CallSid", str(uuid.uuid4()))
    direction = request.form.get("Direction", "").lower()
    user_input = request.form.get("SpeechResult", "").strip()
    print(f"🗣️ [{sid}] Transcribed: {user_input}", flush=True)

    if "goodbye" in user_input.lower():
        clear_conversation(sid)
        return Response("<Response><Say>Okay, goodbye! Thanks for calling.</Say><Hangup/></Response>", mimetype="application/xml")

    history = load_conversation(sid)

    if not history:
        if direction == "inbound":
            greeting = "Hey, this is Nick with AhCHOO! Indoor Air Quality Specialists. How can I help you?"
        elif direction == "outbound-api":
            greeting = "Hey, this is Nick with AhCHOO Indoor Air Quality Specialists. You submitted an action form looking to get some information on our air duct cleaning & HVAC sanitation process. What is your zip code so I can make sure we service your area?"
        else:
            greeting = "Hi, this is Nick from AhCHOO! Indoor Air Quality Specialists. How can I help you?"

        history = [{"role": "assistant", "content": greeting}]
        save_conversation(sid, history)

        return generate_tts_and_response(greeting)

    # FAQ Shortcut
    for keyword, answer in FAQ_RESPONSES.items():
        if keyword in user_input.lower():
            print(f"⚡ FAQ matched: {keyword}")
            return generate_tts_and_response(answer)

    user_zip = extract_zip_or_city(user_input)
    calendar_reply = ""
    if user_zip:
        try:
            creds = load_credentials()
            service = build('calendar', 'v3', credentials=creds)
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            events = service.events().list(calendarId='primary', timeMin=now,
                                           maxResults=10, singleEvents=True,
                                           orderBy='startTime').execute().get('items', [])
            matches = get_calendar_zip_matches(user_zip, events)
            calendar_reply = build_zip_prompt(user_zip, matches)
            print(f"📅 Calendar reply: {calendar_reply}")
        except Exception as e:
            print("❌ Calendar error:", e)
            calendar_reply = "Sorry, I couldn't access our calendar, but I can still help you schedule something."

    if not any(m.get("role") == "system" for m in history):
        history.insert(0, {
            "role": "system",
            "content": "You are Nick from AH-CHOO! Indoor Air Quality Specialists. Be helpful, friendly, and professional. Ask for ZIP codes and schedule estimates using availability."
        })

    history.append({"role": "user", "content": user_input})
    if calendar_reply:
        history.append({"role": "assistant", "content": calendar_reply})

    save_conversation(sid, history)

    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=history
    )
    reply = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})
    save_conversation(sid, history)

    return generate_tts_and_response(reply)

def generate_tts_and_response(text):
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
                "stability": 0.85,
                "similarity_boost": 1.0
            }
        }
    )

    if tts.status_code != 200:
        print("❌ ElevenLabs error:", tts.status_code, tts.text)
        return Response("<Response><Say>Sorry, something went wrong with the voice system.</Say></Response>", mimetype="application/xml")

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








       




