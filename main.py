import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
from flask import Flask, request, Response, send_from_directory, redirect
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

# Debug-Enhanced TTS
def synthesize_speech(text):
    print("Calling ElevenLabs TTS with text:", text)
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
    print("ElevenLabs status code:", tts.status_code)
    if tts.status_code != 200:
        print("❌ ElevenLabs error:", tts.status_code, tts.text)
        return None
    filename = f"{uuid.uuid4()}.mp3"
    filepath = f"static/{filename}"
    try:
        with open(filepath, "wb") as f:
            f.write(tts.content)
        print("MP3 file written at:", filepath)
    except Exception as file_err:
        print("❌ Error writing MP3 file:", file_err)
        return None
    return filename  # just the filename

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
    print(f"Loaded conversation for {sid}: {data}")
    return json.loads(data.decode()) if data else []

def save_conversation(sid, history):
    key = f"history:{sid}"
    if len(history) > 7:
        history = history[:1] + history[-6:]
    redis_client.set(key, json.dumps(history), ex=3600)
    print(f"Saved conversation for {sid}")

def clear_conversation(sid):
    key = f"history:{sid}"
    redis_client.delete(key)

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

# ========== GREETING ON INBOUND CALL (Catbox version) ==========
@app.route("/voice-greeting", methods=["POST", "GET"])
def voice_greeting():
    sid = request.values.get("CallSid") or request.values.get("sid") or request.args.get("sid") or str(uuid.uuid4())
    greeting_url = "https://files.catbox.moe/lmmt31.mp3"
    print(f"New inbound call, SID: {sid}. Greeting will play from {greeting_url}")
    return Response(f"""
    <Response>
        <Play>{greeting_url}</Play>
        <Redirect method="POST">/response?sid={sid}</Redirect>
    </Response>
    """, mimetype="application/xml")

@app.route("/response", methods=["POST", "GET"])
def response_route():
    # SET THIS TO TRUE TO TEST WITH HARDCODED TEXT, BYPASSING OPENAI/GOOGLE
    HARD_CODED_MODE = False

    sid = (
        request.values.get("sid")
        or request.args.get("sid")
        or request.values.get("CallSid")
        or request.args.get("CallSid")
    )
    print(f"SID received in /response: {sid}")

    if not sid:
        print("❌ SID missing!")
        return Response("<Response><Say>Missing session ID.</Say></Response>", mimetype="application/xml")

    history = load_conversation(sid)
    user_zip = redis_client.get(f"zip:{sid}")
    user_zip = user_zip.decode() if user_zip else None
    print(f"user_zip: {user_zip}")

    if HARD_CODED_MODE:
        gpt_reply = "Hello, this is a test of ElevenLabs speech and your static folder. If you hear this, everything is working up to this point!"
    else:
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
            import traceback
            tb = traceback.format_exc()
            print(f"❌ GPT generation error: {e}\n{tb}")
            return Response("<Response><Say>Sorry, there was an error processing your request.</Say></Response>", mimetype="application/xml")

    print(f"🤖 GPT reply to synthesize: {gpt_reply}")
    history.append({"role": "assistant", "content": gpt_reply})
    save_conversation(sid, history)

    reply_filename = synthesize_speech(gpt_reply)
    print("Reply filename to play:", reply_filename)
    if not reply_filename:
        print("❌ Failed to synthesize speech or save file!")
        return Response("<Response><Say>Sorry, there was an error playing the response.</Say></Response>", mimetype="application/xml")

    play_url = f"https://{request.host}/static/{reply_filename}"
    print("Returning TwiML to play:", play_url)
    return Response(f"""
    <Response>
        <Play>{play_url}</Play>
        <Gather input="speech" action="/voice?sid={sid}" method="POST" timeout="5" />
    </Response>
    """, mimetype="application/xml")

@app.route("/voice", methods=["POST"])
def voice_route():
    sid = (
        request.values.get("sid")
        or request.args.get("sid")
        or request.values.get("CallSid")
        or request.args.get("CallSid")
    )
    user_input = request.values.get("SpeechResult", "")
    print(f"Received voice input: {user_input}")

    history = load_conversation(sid)
    if user_input:
        history.append({"role": "user", "content": user_input})
        zip_found = extract_zip_or_city(user_input)
        if zip_found:
            redis_client.set(f"zip:{sid}", zip_found, ex=900)
    save_conversation(sid, history)

    print(f"Redirecting back to /response for SID {sid}")
    return redirect(f"/response?sid={sid}")

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

@app.route("/static-test", methods=["GET"])
def static_test():
    test_path = "static/testfile.txt"
    try:
        with open(test_path, "w") as f:
            f.write("STATIC FOLDER WRITE SUCCESS!")
        return "✅ Successfully wrote to static/testfile.txt!"
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return f"❌ FAILED to write file: {str(e)}\n\n{tb}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
