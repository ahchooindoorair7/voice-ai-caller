import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
import random
import concurrent.futures
import time
import dateutil.parser  # <-- Added for better datetime formatting
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
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# === CATBOX THINKING MP3 URLs ===
THINKING_MP3_URLS = [
    "https://files.catbox.moe/pxo487.mp3",
    "https://files.catbox.moe/s5b3c0.mp3",
    "https://files.catbox.moe/6mwzq8.mp3",
    "https://files.catbox.moe/pi9kkx.mp3",
    "https://files.catbox.moe/oqos6c.mp3",
    "https://files.catbox.moe/4ydptg.mp3",
    "https://files.catbox.moe/dkmme7.mp3",
    "https://files.catbox.moe/in2w67.mp3",
    "https://files.catbox.moe/zzpn3e.mp3",
    "https://files.catbox.moe/e8he55.mp3",
    "https://files.catbox.moe/sr9baf.mp3",
    "https://files.catbox.moe/3mf7m8.mp3"
]

def get_next_thinking_url(sid):
    key_played = f"thinking_played:{sid}"
    played = redis_client.lrange(key_played, 0, -1)
    played = [x.decode("utf-8") for x in played]
    not_played = [msg for msg in THINKING_MP3_URLS if msg not in played]
    if not_played:
        next_msg = random.choice(not_played)
        redis_client.rpush(key_played, next_msg)
        redis_client.expire(key_played, 900)
        return next_msg
    else:
        redis_client.delete(key_played)
        next_msg = random.choice(THINKING_MP3_URLS)
        redis_client.rpush(key_played, next_msg)
        redis_client.expire(key_played, 900)
        return next_msg

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

# === NEW: Nicely format calendar times for agent responses ===
def format_event_time(dt_str):
    try:
        dt = dateutil.parser.parse(dt_str)
        day_suffix = lambda d: 'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')
        # Use %-I if your environment supports it; otherwise, switch to %I for zero-padded hours.
        formatted_date = dt.strftime(f"%A, %B {dt.day}{day_suffix(dt.day)} at %-I:%M %p")
        return formatted_date
    except Exception as e:
        print(f"Failed to format event time: {dt_str} - {e}")
        return dt_str

def build_zip_prompt(user_zip, matches):
    if matches:
        formatted_times = [format_event_time(dt) for dt in matches[:2]]
        return f"We’ll already be in your area ({user_zip}) at {', '.join(formatted_times)}. Would one of those work for a free estimate?"
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
    if len(history) > 15:
        history = history[:1] + history[-15:]
    redis_client.set(key, json.dumps(history), ex=3600)
    print(f"Saved conversation for {sid}")
    print(f"LEAVING save_conversation({sid})")

def clear_conversation(sid):
    print(f"ENTER clear_conversation({sid})")
    key = f"history:{sid}"
    redis_client.delete(key)
    print(f"LEAVING clear_conversation({sid})")

def async_generate_response(sid, messages, user_zip):
    def task():
        try:
            print(f"[Async] Starting async_generate_response for {sid}")
            gpt_reply = ""
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
                messages.append({"role": "assistant", "content": calendar_prompt})

            print(f"[Async] OpenAI chat history for {sid}: {messages}")
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                stream=True
            )
            for chunk in response:
                if hasattr(chunk.choices[0].delta, "content"):
                    gpt_reply += chunk.choices[0].delta.content or ""
            print(f"[Async] Final GPT reply for {sid}: '{gpt_reply}'")

            history = load_conversation(sid)
            history.append({"role": "assistant", "content": gpt_reply})
            save_conversation(sid, history)

            reply_filename = synthesize_speech(gpt_reply)
            if reply_filename:
                redis_client.set(f"pending_mp3:{sid}", reply_filename, ex=600)
                print(f"[Async] Saved mp3 '{reply_filename}' for {sid}")
            else:
                redis_client.set(f"pending_mp3:{sid}", "error", ex=600)
                print(f"[Async] Failed TTS for {sid}")
        except Exception as e:
            redis_client.set(f"pending_mp3:{sid}", "error", ex=600)
            print(f"[Async] Exception in async_generate_response for {sid}: {e}")
    executor.submit(task)

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
    answered_by = (request.values.get("AnsweredBy") or "").lower()
    print(f"AnsweredBy: {answered_by}")
    if answered_by in ["machine", "fax", "unknown_machine"]:
        print("Detected answering machine. Redirecting to /voicemail for voicemail drop.")
        return redirect("/voicemail", code=307)  # 307 keeps POST data
    print(f"New inbound/outbound call, SID: {sid}. Greeting will play from {greeting_url}")
    print("LEAVING /voice-greeting")
    return Response(f"""
    <Response>
        <Play>{greeting_url}</Play>
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

    # === Build GPT messages and kick off async ===
    SYSTEM_PROMPT = {
        "role": "system",
        "content": (
             "You are a helpful sales assistant for a premium high end air duct cleaning company that has been in business for 37 years. "
            "Backed by our 5 star review rating on all platforms, we are the most high end air quality company you can find. "
            "We are a state licensed mold remediation contractor, & we do dryer vent cleaning for free when we clean the HVAC system as well. Respond conversationally & professionally. "
            "Great customer service is very important. If it is an outbound call then your goal should be to book them for a free estimate by asking for their ZIP code. "
            "Then cross reference our google calendar to find a time we will be in their area. If it is an inbound call & they say they are looking to get a quote, price, or estimate, your goal should be to book an estimate. "
            "UNDER NO CIRCUMSTANCES should you ever give a price, quote, average, ballpark, or estimate over the phone or via text. "
            "If the customer asks for a price, quote, estimate, or ballpark, politely explain that our company policy is to do a free in-person inspection so we can give the most accurate, customized estimate based on the specific needs of their home. We don't like to play the add-on or upcharge game. Whatever price we give you will stay there!"
            "Always redirect the conversation toward booking a free in-person estimate, never giving any numbers. "
            "Sample response: 'We actually never give prices or ballpark estimates over the phone because every home is different. We don't like to play the add-on or upcharge game. So whatever price we give you will stay there. We take pride in doing things the right way. What day works best for you to have one of our experts come out?'"
        )
    }
    messages = [SYSTEM_PROMPT]
    for msg in history:
        if msg.get("role") == "system":
            continue
        messages.append(msg)
    user_zip = redis_client.get(f"zip:{sid}")
    user_zip = user_zip.decode() if user_zip else None
    async_generate_response(sid, messages, user_zip)

    thinking_url = get_next_thinking_url(sid)
    print("Playing thinking message:", thinking_url)
    print(f"Redirecting to /response for SID {sid}")
    print("LEAVING /voice")
    return Response(f"""
    <Response>
        <Play>{thinking_url}</Play>
        <Redirect method="POST">/response?sid={sid}</Redirect>
    </Response>
    """, mimetype="application/xml")

@app.route("/response", methods=["POST", "GET"])
def response_route():
    print("ENTER /response_route")  # <----- PINPOINT LOG
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

    # Wait up to 7.5 seconds for async reply to finish
    for _ in range(15):
        mp3_filename = redis_client.get(f"pending_mp3:{sid}")
        if mp3_filename:
            mp3_filename = mp3_filename.decode()
            if mp3_filename == "error":
                print(f"❌ Failed to synthesize speech or save file! Returning error to caller for SID {sid}")
                print("LEAVING /response_route (TTS fail)")
                return Response("<Response><Say>Sorry, there was an error playing the response.</Say></Response>", mimetype="application/xml")
            play_url = f"https://{request.host}/static/{mp3_filename}"
            print("Returning TwiML to play:", play_url)
            print("LEAVING /response_route (SUCCESS)")
            return Response(f"""
            <Response>
                <Play>{play_url}</Play>
                <Gather input="speech" action="/voice?sid={sid}" method="POST" timeout="5" speechTimeout="auto" />
            </Response>
            """, mimetype="application/xml")
        time.sleep(0.5)
    # Timed out waiting for reply
    print("❌ Timed out waiting for async reply. Returning error to caller for SID", sid)
    print("LEAVING /response_route (timeout)")
    return Response("<Response><Say>Sorry, your response took too long to generate. Please try again.</Say></Response>", mimetype="application/xml")

@app.route("/voicemail", methods=["POST", "GET"])
def voicemail_route():
    voicemail_url = "https://files.catbox.moe/0ugvt7.mp3"
    print(f"Voicemail drop initiated, playing: {voicemail_url}")
    return Response(f"""
    <Response>
        <Play>{voicemail_url}</Play>
        <Hangup/>
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
