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
import dateutil.parser
import asyncio
import websockets
import base64
from flask import Flask, request, Response, redirect
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# --- ENV VARS
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")
OWNER_PHONE_NUMBER = os.environ.get("OWNER_PHONE_NUMBER")

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
city_to_zip = {
    "houston": "77002", "sugar land": "77479", "katy": "77494",
    "the woodlands": "77380", "cypress": "77429", "bellaire": "77401", "tomball": "77375"
}

redis_client = redis.from_url(REDIS_URL)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

GREETING_MP3_URL = "https://files.catbox.moe/lmmt31.mp3"
CLOSING_MP3_URL = "https://files.catbox.moe/sxjdxb.mp3"

# --- FLASK APP FOR TWILIO HOOKS
app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# ---- MEMORY/BOOKING LOGIC PRESERVED FROM YOUR OLD main.py

def format_event_time(dt_str):
    try:
        dt = dateutil.parser.parse(dt_str)
        day_suffix = lambda d: 'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')
        formatted_date = dt.strftime(f"%A, %B {dt.day}{day_suffix(dt.day)} at %-I:%M %p")
        return formatted_date
    except Exception as e:
        print(f"Failed to format event time: {dt_str} - {e}")
        return dt_str

def get_calendar_zip_matches(user_zip, events):
    matches = []
    for event in events:
        location = event.get('location', '')
        match = re.search(r'\b77\d{3}\b', location)
        if match and match.group(0) == user_zip:
            start = event['start'].get('dateTime', event['start'].get('date'))
            matches.append(start)
    return matches

def load_credentials():
    print("ENTER load_credentials()")
    token_json = os.environ.get("GOOGLE_TOKEN")
    if not token_json:
        print("❌ No GOOGLE_TOKEN environment variable found.")
        return None
    try:
        data = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(data, SCOPES)
        return creds
    except Exception as e:
        print("❌ Failed to load credentials from GOOGLE_TOKEN:", e)
        return None

def load_conversation(sid):
    key = f"history:{sid}"
    data = redis_client.get(key)
    return json.loads(data.decode()) if data else []

def save_conversation(sid, history):
    key = f"history:{sid}"
    if len(history) > 15:
        history = history[:1] + history[-15:]
    redis_client.set(key, json.dumps(history), ex=3600)

def clear_conversation(sid):
    key = f"history:{sid}"
    redis_client.delete(key)

def text_booking_to_owner(date_time, address, phone, notes=None):
    try:
        if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER and OWNER_PHONE_NUMBER):
            print("Twilio SMS notification vars not all set!")
            return False
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = (
            f"🗓️ New Air Duct Estimate Booking!\n"
            f"Address: {address}\n"
            f"Date/Time: {date_time}\n"
            f"Phone: {phone}\n"
        )
        if notes:
            message += f"Notes: {notes}"
        client.messages.create(
            to=OWNER_PHONE_NUMBER,
            from_=TWILIO_FROM_NUMBER,
            body=message
        )
        print("Texted booking to owner successfully.")
        return True
    except Exception as e:
        print(f"Failed to send booking SMS: {e}")
        return False

def get_ai_functions():
    return [
        {
            "name": "book_estimate",
            "description": "Books a free in-person estimate for air duct cleaning. Only call this function when you have confirmed the customer's ZIP, full address, and agreed upon appointment date/time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zip_code": {"type": "string", "description": "ZIP code"},
                    "address": {"type": "string", "description": "Full street address including city and ZIP"},
                    "date_time": {"type": "string", "description": "Confirmed appointment date and time, e.g. 'Tuesday, June 4th at 10:00 AM'"},
                },
                "required": ["zip_code", "address", "date_time"]
            }
        }
    ]

# ---- GREETING HOOK (TWILIO POINTS TO THIS BEFORE MEDIA STREAM)
@app.route("/voice-greeting", methods=["POST", "GET"])
def voice_greeting():
    sid = request.values.get("CallSid") or request.values.get("sid") or request.args.get("sid") or str(uuid.uuid4())
    answered_by = (request.values.get("AnsweredBy") or "").lower()
    if answered_by in ["machine", "fax", "unknown_machine"]:
        return redirect("/voicemail", code=307)
    return Response(f"""
    <Response>
        <Play>{GREETING_MP3_URL}</Play>
        <Start>
            <Stream url="wss://{request.host}/ws?sid={sid}" />
        </Start>
    </Response>
    """, mimetype="application/xml")

@app.route("/voicemail", methods=["POST", "GET"])
def voicemail_route():
    voicemail_url = "https://files.catbox.moe/0ugvt7.mp3"
    return Response(f"""
    <Response>
        <Play>{voicemail_url}</Play>
        <Hangup/>
    </Response>
    """, mimetype="application/xml")

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

# --- ASYNC WEBSOCKET SERVER FOR TWILIO MEDIA STREAMS
# --- (Runs in the same process as Flask for Render deployment)

async def process_media_stream(websocket, path):
    # Extract SID for conversation memory
    query = websocket.path.split("?")
    sid = None
    if len(query) > 1:
        from urllib.parse import parse_qs
        params = parse_qs(query[1])
        sid = params.get("sid", [str(uuid.uuid4())])[0]
    else:
        sid = str(uuid.uuid4())
    caller_number = None

    print(f"[WS] New Twilio media stream. SID={sid}")

    # Set up AssemblyAI real-time session
    aai_url = "wss://api.assemblyai.com/v2/realtime/ws?sample_rate=8000"
    headers = {"Authorization": ASSEMBLYAI_API_KEY}
    async with websockets.connect(aai_url, extra_headers=headers) as aai_ws:
        async def send_to_assemblyai():
            async for message in websocket:
                try:
                    msg = json.loads(message)
                    if msg["event"] == "start":
                        caller_number = msg.get("start", {}).get("call_sid")
                        print(f"[WS] Start event. Caller: {caller_number}")
                    elif msg["event"] == "media":
                        audio = base64.b64decode(msg["media"]["payload"])
                        await aai_ws.send(audio)
                except Exception as e:
                    print("[WS] Error forwarding to AssemblyAI:", e)

        async def receive_from_assemblyai():
            transcript_buf = ""
            async for msg in aai_ws:
                try:
                    data = json.loads(msg)
                    if "text" in data and data["message_type"] == "FinalTranscript":
                        transcript = data["text"].strip()
                        if transcript:
                            print(f"[WS] Transcript: {transcript}")
                            # Save to memory
                            history = load_conversation(sid)
                            history.append({"role": "user", "content": transcript})
                            save_conversation(sid, history)

                            # Check for ZIP in transcript
                            zip_found = re.search(r'\b77\d{3}\b', transcript)
                            if zip_found:
                                redis_client.set(f"zip:{sid}", zip_found.group(0), ex=900)
                            if caller_number:
                                redis_client.set(f"phone:{sid}", caller_number, ex=900)

                            # --- Compose prompt (preserve your logic)
                            SYSTEM_PROMPT = {
                                "role": "system",
                                "content": (
                                    "You are a helpful sales assistant for a premium high end air duct cleaning company that has been in business for 37 years. "
        "Backed by our 5 star review rating on all platforms, we are the most high end air quality company you can find. "
        "We are a state licensed mold remediation contractor, & we do dryer vent cleaning for free when we clean the HVAC system as well. Respond conversationally & professionally. "
        "Great customer service is very important. If it is an outbound call then your goal should be to book them for a free estimate by asking for their ZIP code. "
        "Then cross reference our google calendar to find a time we will be in their area. If it is an inbound call & they say they are looking to get a quote, price, or estimate, your goal should be to book an estimate. "More actions
        "UNDER NO CIRCUMSTANCES should you ever give a price, quote, average, ballpark, or estimate over the phone or via text. "
        "If the customer asks for a price, quote, estimate, or ballpark, politely explain that our company policy is to do a free in-person inspection so we can give the most accurate, customized estimate based on the specific needs of their home. We don't like to play the add-on or upcharge game. Whatever price we give you will stay there!"
        "Always redirect the conversation toward booking a free in-person estimate, never giving any numbers. "
        "Sample response: 'We actually never give prices or ballpark estimates over the phone because every home is different. We don't like to play the add-on or upcharge game. So whatever price we give you will stay there. We take pride in doing things the right way. What day works best for you to have one of our experts come out?'"
                                )
                            }
                            user_zip = redis_client.get(f"zip:{sid}")
                            user_zip = user_zip.decode() if user_zip else None
                            messages = [SYSTEM_PROMPT] + [msg for msg in history if msg.get("role") != "system"]

                            # Calendar prompt injection (if applicable)
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
                                formatted_times = [format_event_time(dt) for dt in matches[:2]]
                                if formatted_times:
                                    calendar_prompt = f"We’ll already be in your area ({user_zip}) at {', '.join(formatted_times)}. Would one of those work for a free estimate?"
                                else:
                                    calendar_prompt = f"We’re not currently scheduled in {user_zip}, but I can open up time for you. What day & time works best?"
                                messages.append({"role": "assistant", "content": calendar_prompt})

                            # --- GPT-4o streaming call
                            openai.api_key = OPENAI_API_KEY
                            ai_functions = get_ai_functions()
                            response = openai.chat.completions.create(
                                model="gpt-4o",
                                messages=messages,
                                functions=ai_functions,
                                function_call="auto",
                                stream=True
                            )
                            reply_text = ""
                            function_called = None
                            fn_args = {}
                            for chunk in response:
                                if hasattr(chunk.choices[0].delta, "content"):
                                    content = chunk.choices[0].delta.content
                                    if content:
                                        reply_text += content
                                        # --- Stream to ElevenLabs as we go
                                        await elevenlabs_stream_tts(content, websocket)
                                if hasattr(chunk.choices[0].delta, "function_call"):
                                    function_called = chunk.choices[0].delta.function_call.name
                                    fn_args = json.loads(chunk.choices[0].delta.function_call.arguments)

                            # Function-calling logic
                            if function_called == "book_estimate":
                                redis_client.set(f"zip:{sid}", fn_args["zip_code"], ex=900)
                                redis_client.set(f"address:{sid}", fn_args["address"], ex=900)
                                redis_client.set(f"time:{sid}", fn_args["date_time"], ex=900)
                                redis_client.set(f"notified:{sid}", "1", ex=1800)
                                text_booking_to_owner(fn_args["date_time"], fn_args["address"], caller_number)
                                reply_text = (
                                    f"You're all set! We have you down for a free estimate at {fn_args['address']} on {fn_args['date_time']}. "
                                    "We'll send you a confirmation shortly. Thank you!"
                                )
                                await elevenlabs_stream_tts(reply_text, websocket)
                                # Play closing MP3
                                await stream_mp3_to_twilio(CLOSING_MP3_URL, websocket)

                            # Save assistant message
                            history = load_conversation(sid)
                            history.append({"role": "assistant", "content": reply_text})
                            save_conversation(sid, history)

                except Exception as e:
                    print("[WS] Error in AssemblyAI recv:", e)

        async def elevenlabs_stream_tts(text, twilio_ws):
            if not text.strip():
                return
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
            headers = {
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            }
            body = {
                "text": text,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.7},
                "model_id": "eleven_multilingual_v2"
            }
            resp = requests.post(url, headers=headers, json=body, stream=True)
            chunk_size = 2048
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    media_msg = {
                        "event": "media",
                        "media": {"payload": base64.b64encode(chunk).decode()}
                    }
                    await twilio_ws.send(json.dumps(media_msg))

        async def stream_mp3_to_twilio(mp3_url, twilio_ws):
            # Download MP3 and send chunks as Twilio media events
            resp = requests.get(mp3_url, stream=True)
            for chunk in resp.iter_content(2048):
                if chunk:
                    media_msg = {
                        "event": "media",
                        "media": {"payload": base64.b64encode(chunk).decode()}
                    }
                    await twilio_ws.send(json.dumps(media_msg))

        await asyncio.gather(send_to_assemblyai(), receive_from_assemblyai())

# --- RUN BOTH FLASK (FOR HOOKS) AND WS (FOR MEDIA STREAM) ON RENDER
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

def run_ws():
    ws_port = 8765
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    start_server = websockets.serve(process_media_stream, "0.0.0.0", ws_port)
    loop.run_until_complete(start_server)
    loop.run_forever()

if __name__ == "__main__":
    import threading
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    run_ws()
