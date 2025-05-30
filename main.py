import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
import random  # Needed for random thinking MP3 selection
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

# === CATBOX THINKING MP3 URLs ===
THINKING_MP3_URLS = [
    "https://files.catbox.moe/syinri.mp3",
    "https://files.catbox.moe/nmeg94.mp3",
    "https://files.catbox.moe/a57hqt.mp3"
]

def get_random_thinking_url():
    return random.choice(THINKING_MP3_URLS)

def synthesize_speech(text):
    print("ENTER synthesize_speech()")
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
        print("Returning error: Sorry, there was an error playing the response.")
        print("LEAVING synthesize_speech()")
        return None
    filename = f"{uuid.uuid4()}.mp3"
    filepath = f"static/{filename}"
    try:
        with open(filepath, "wb") as f:
            f.write(tts.content)
        print("MP3 file written at:", filepath)
    except Exception as file_err:
        print("❌ Error writing MP3 file:", file_err)
        print("Returning error: Sorry, there was an error playing the response.")
        print("LEAVING synthesize_speech()")
        return None
    print("LEAVING synthesize_speech()")
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
        return f"We’re not currently scheduled in {user_zip}, but I can open up time for you. What day & time works best?"

def load_credentials():
    print("ENTER load_credentials()")
    token_json = os.environ.get("GOOGLE_TOKEN")
    if not token_json:
        print("❌ No GOOGLE_TOKEN environment variable found.")
        print("Returning error: Sorry, there was an error processing your request (missing Google token).")
        print("LEAVING load_credentials()")
        return None
    try:
        data = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(data, SCOPES)
        print("LEAVING load_credentials()")
        return creds
    except Exception as e:
        print("❌ Failed to load credentials from GOOGLE_TOKEN:", e)
        print("Returning error: Sorry, there was an error processing your request (bad Google token).")
        print("LEAVING load_credentials()")
        return None

def load_conversation(sid):
    print(f"ENTER load_conversation({sid})")
    key = f"history:{sid}"
    data = redis_client.get(key)
    print(f"Loaded conversation for {sid}: {data}")
    print(f"LEAVING load_conversation({sid})")
    return json.loads(data.decode()) if data else []

def save_conversation(sid, history):
    print(f"ENTER save_conversation({sid})")
    key = f"history:{sid}"
    if len(history) > 7:
        history = history[:1] + history[-6:]
    redis_client.set(key, json.dumps(history), ex=3600)
    print(f"Saved conversation for {sid}")
    print(f"LEAVING save_conversation({sid})")

def clear_conversation(sid):
    print(f"ENTER clear_conversation({sid})")
    key = f"history:{sid}"
    redis_client.delete(key)
    print(f"LEAVING clear_conversation({sid})")

@app.route("/test-openai", methods=["GET"])
def test_openai():
    print("ENTER /test-openai")
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello!"}
            ]
        )
        print("LEAVING /test-openai")
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ OpenAI API test failed: {e}")
        print("LEAVING /test-openai")
        return f"❌ OpenAI API test failed: {e}"

@app.route("/static/<path:filename>")
def static_files(filename):
    print("ENTER /static/<path:filename>")
    response = send_from_directory('static', filename)
    print("LEAVING /static/<path:filename>")
    return response

@app.route("/voice-greeting", methods=["POST", "GET"])
def voice_greeting():
    print("ENTER /voice-greeting")
    sid = request.values.get("CallSid") or request.values.get("sid") or request.args.get("sid") or str(uuid.uuid4())
    greeting_url = "https://files.catbox.moe/lmmt31.mp3"
    print(f"New inbound call, SID: {sid}. Greeting will play from {greeting_url}")
    print("LEAVING /voice-greeting")
    # *** FIXED: No redirect to /response; just gather speech after greeting! ***
    return Response(f"""
    <Response>
        <Play>{greeting_url}</Play>
        <Gather input="speech" action="/voice?sid={sid}" method="POST" timeout="5" speechTimeout="auto" />
    </Response>
    """, mimetype="application/xml")

@app.route("/response", methods=["POST", "GET"])
def response_route():
    print("ENTER /response_route")  # <----- PINPOINT LOG
    HARD_CODED_MODE = False

    sid = (
        request.values.get("sid")
        or request.args.get("sid")
        or request.values.get("CallSid")
        or request.args.get("CallSid")
    )
    print(f"SID received in /response: {sid}")

    if not sid:
        print("❌ SID missing! Returning early error (missing session ID).")
        print("LEAVING /response_route (SID missing)")
        return Response("<Response><Say>Missing session ID.</Say></Response>", mimetype="application/xml")

    history = load_conversation(sid)
    user_zip = redis_client.get(f"zip:{sid}")
    user_zip = user_zip.decode() if user_zip else None
    print(f"user_zip: {user_zip}")

    # SYSTEM PROMPT - This will **always** be the first message
    SYSTEM_PROMPT = {
        "role": "system",
        "content": (
            "You are a helpful sales assistant for a premium high end air duct cleaning company that has been in business for 37 years. "
            "Backed by our 5 star review rating on all platforms, we are the most high end air quality company you can find. "
            "We are a state licensed mold remediation contractor as well. Respond conversationally & professionally. "
            "Great customer service is very important. If it is an outbound call then your goal should be to book them for a free estimate by asking for their ZIP code. "
            "Then cross reference our google calendar to find a time we will be in their area. If it is an inbound call & they say they are looking to get a quote, price, or estimate... "
            "then your goal should be to book an estimate. So ask them for their ZIP code at that point to cross reference it with the calendar."
        )
    }

    # Ensure SYSTEM_PROMPT is ALWAYS FIRST in messages
    messages = [SYSTEM_PROMPT]
    for msg in history:
        if msg.get("role") == "system":
            continue  # skip any existing system prompts
        messages.append(msg)

    print("About to enter try/except block")

    if HARD_CODED_MODE:
        gpt_reply = "Hello, this is a test of ElevenLabs speech and your static folder. If you hear this, everything is working up to this point!"
    else:
        gpt_reply = ""
        try:
            if user_zip:
                creds = load_credentials()
                if not creds:
                    print("❌ Failed to get Google creds. Returning error.")
                    print("LEAVING /response_route (Google creds fail)")
                    return Response("<Response><Say>Sorry, there was an error processing your request.</Say></Response>", mimetype="application/xml")
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                service = build('calendar', 'v3', credentials=creds)
                now = datetime.datetime.utcnow().isoformat() + 'Z'
                events = service.events().list(calendarId='primary', timeMin=now,
                                               maxResults=10, singleEvents=True,
                                               orderBy='startTime').execute().get('items', [])
                matches = get_calendar_zip_matches(user_zip, events)
                calendar_prompt = build_zip_prompt(user_zip, matches)
                messages.append({"role": "assistant", "content": calendar_prompt})

            print("OpenAI chat history:", messages)
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                stream=True
            )
            for chunk in response:
                print('Got OpenAI chunk:', chunk)
                if hasattr(chunk.choices[0].delta, "content"):
                    gpt_reply += chunk.choices[0].delta.content or ""
            print(f"Final GPT reply: '{gpt_reply}'")

            if not gpt_reply.strip():
                print("GPT reply was empty after OpenAI call. Returning error to caller.")
                gpt_reply = "Sorry, there was an issue with my response. Can you try again?"

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"❌ GPT generation error: {e}\n{tb}")
            print("Returning error: Sorry, there was an error processing your request (OpenAI/other except).")
            print("LEAVING /response_route (OpenAI fail)")
            return Response("<Response><Say>Sorry, there was an error processing your request.</Say></Response>", mimetype="application/xml")

    print(f"🤖 GPT reply to synthesize: {gpt_reply}")
    history.append({"role": "assistant", "content": gpt_reply})
    save_conversation(sid, history)

    reply_filename = synthesize_speech(gpt_reply)
    print("Reply filename to play:", reply_filename)
    if not reply_filename:
        print("❌ Failed to synthesize speech or save file! Returning error to caller.")
        print("LEAVING /response_route (TTS fail)")
        return Response("<Response><Say>Sorry, there was an error playing the response.</Say></Response>", mimetype="application/xml")

    play_url = f"https://{request.host}/static/{reply_filename}"
    print("Returning TwiML to play:", play_url)
    print("LEAVING /response_route (SUCCESS)")
    return Response(f"""
    <Response>
        <Play>{play_url}</Play>
        <Gather input="speech" action="/voice?sid={sid}" method="POST" timeout="5" speechTimeout="auto" />
    </Response>
    """, mimetype="application/xml")

@app.route("/voice", methods=["POST"])
def voice_route():
    print("ENTER /voice")
    sid = (
        request.values.get("sid")
        or request.args.get("sid")
        or request.values.get("CallSid")
        or request.args.get("CallSid")
    )
    user_input = request.values.get("SpeechResult", "")
    print(f"Received voice input: '{user_input}'")

    history = load_conversation(sid)
    if user_input:
        history.append({"role": "user", "content": user_input})
        zip_found = extract_zip_or_city(user_input)
        if zip_found:
            redis_client.set(f"zip:{sid}", zip_found, ex=900)
    else:
        print("❌ No speech recognized from caller.")
    save_conversation(sid, history)

    # Play a random thinking mp3, then redirect to /response
    thinking_url = get_random_thinking_url()
    print("Playing thinking message:", thinking_url)
    print(f"Redirecting to /response for SID {sid}")
    print("LEAVING /voice")
    return Response(f"""
    <Response>
        <Play>{thinking_url}</Play>
        <Redirect method="POST">/response?sid={sid}</Redirect>
    </Response>
    """, mimetype="application/xml")

@app.route("/", methods=["GET"])
def root():
    print("ENTER / (root)")
    print("LEAVING / (root)")
    return "Nick AI Voice Agent is running."

@app.route("/static-test", methods=["GET"])
def static_test():
    print("ENTER /static-test")
    test_path = "static/testfile.txt"
    try:
        with open(test_path, "w") as f:
            f.write("STATIC FOLDER WRITE SUCCESS!")
        print("LEAVING /static-test (SUCCESS)")
        return "✅ Successfully wrote to static/testfile.txt!"
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"❌ FAILED to write file: {str(e)}\n\n{tb}")
        print("LEAVING /static-test (FAIL)")
        return f"❌ FAILED to write file: {str(e)}\n\n{tb}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
