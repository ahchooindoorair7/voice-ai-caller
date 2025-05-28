import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
from flask import Flask, request, Response, send_from_directory, redirect  # FIXED: no backslash or underscore
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
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
                if os.path.isfile(filepath) and not (
                    filepath.startswith(os.path.join(folder, "thinking")) or
                    filepath.startswith(os.path.join(folder, "thinking_zip"))
                ):
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
    key = f"history:{sid}"
    data = redis_client.get(key)
    return json.loads(data.decode()) if data else []

def save_conversation(sid, history):
    key = f"history:{sid}"
    if len(history) > 7:
        history = history[:1] + history[-6:]
    redis_client.set(key, json.dumps(history), ex=3600)

def clear_conversation(sid):
    key = f"history:{sid}"
    redis_client.delete(key)

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
    return filename  # just the filename

@app.route("/test-openai", methods=["GET"])
def test_openai():
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello!"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ OpenAI API test failed: {e}"

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory('static', filename)

@app.route("/response", methods=["POST", "GET"])
def response_route():
    sid = request.values.get("sid")
    if not sid:
        return Response("<Response><Say>Missing session ID.</Say></Response>", mimetype="application/xml")

    history = load_conversation(sid)
    user_zip = redis_client.get(f"zip:{sid}")
    user_zip = user_zip.decode() if user_zip else None

    gpt_reply = ""
    try:
        # Calendar prompt if zip available
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

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=history,
            stream=True
        )
        for chunk in response:
            if hasattr(chunk.choices[0].delta, "content"):
                gpt_reply += chunk.choices[0].delta.content or ""

        if not gpt_reply.strip():
            gpt_reply = "Sorry, there was an issue with my response. Can you try again?"

    except Exception as e:
        print("❌ GPT generation error:", e)
        return Response("<Response><Say>Sorry, there was an error processing your request.</Say></Response>", mimetype="application/xml")

    print(f"🤖 GPT reply: {gpt_reply}")
    history.append({"role": "assistant", "content": gpt_reply})
    save_conversation(sid, history)

    reply_filename = synthesize_speech(gpt_reply)
    if not reply_filename:
        return Response("<Response><Say>Sorry, there was an error playing the response.</Say></Response>", mimetype="application/xml")

    play_url = f"https://{request.host}/static/{reply_filename}"
    return Response(f"""
    <Response>
        <Play>{play_url}</Play>
        <Gather input="speech" action="/voice" method="POST" timeout="5" />
    </Response>
    """, mimetype="application/xml")

@app.route("/voice", methods=["POST"])
def voice_route():
    # This endpoint should handle incoming speech from Twilio's <Gather>
    sid = request.values.get("sid")
    user_input = request.values.get("SpeechResult", "")
    print(f"Received voice input: {user_input}")

    # Save/append the user's input to conversation history
    history = load_conversation(sid)
    if user_input:
        history.append({"role": "user", "content": user_input})
        # Optionally, extract ZIP code and cache it
        zip_found = extract_zip_or_city(user_input)
        if zip_found:
            redis_client.set(f"zip:{sid}", zip_found, ex=900)
    save_conversation(sid, history)

    # Redirect back to /response to continue the dialogue loop
    return redirect(f"/response?sid={sid}")

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
