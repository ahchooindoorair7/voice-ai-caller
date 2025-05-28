import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
from flask import Flask, request, Response, send\_from\_directory, redirect  # <--- FIXED: Added 'redirect'
from flask\_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

app = Flask(**name**)
app.config\['SESSION\_TYPE'] = 'filesystem'
Session(app)

os.environ\['OAUTHLIB\_INSECURE\_TRANSPORT'] = '1'

OPENAI\_API\_KEY = os.environ.get("OPENAI\_API\_KEY")
ELEVENLABS\_API\_KEY = os.environ.get("ELEVENLABS\_API\_KEY")
ELEVENLABS\_VOICE\_ID = os.environ.get("ELEVENLABS\_VOICE\_ID")
GOOGLE\_TOKEN = os.environ.get("GOOGLE\_TOKEN")
REDIS\_URL = os.environ.get("REDIS\_URL")

SCOPES = \['[https://www.googleapis.com/auth/calendar.readonly](https://www.googleapis.com/auth/calendar.readonly)']
city\_to\_zip = {
"houston": "77002", "sugar land": "77479", "katy": "77494",
"the woodlands": "77380", "cypress": "77429", "bellaire": "77401", "tomball": "77375"
}

redis\_client = redis.from\_url(REDIS\_URL)

PRELOADED\_THINKING\_MESSAGES = \[]
PRELOADED\_ZIP\_THINKING\_MESSAGES = \[]

PRELOADED\_THINKING\_MESSAGES\_FOLDER = "static/thinking"
PRELOADED\_ZIP\_THINKING\_MESSAGES\_FOLDER = "static/thinking\_zip"

if os.path.exists(PRELOADED\_THINKING\_MESSAGES\_FOLDER):
for f in os.listdir(PRELOADED\_THINKING\_MESSAGES\_FOLDER):
if f.endswith(".mp3"):
PRELOADED\_THINKING\_MESSAGES.append(f"static/thinking/{f}")

if os.path.exists(PRELOADED\_ZIP\_THINKING\_MESSAGES\_FOLDER):
for f in os.listdir(PRELOADED\_ZIP\_THINKING\_MESSAGES\_FOLDER):
if f.endswith(".mp3"):
PRELOADED\_ZIP\_THINKING\_MESSAGES.append(f"static/thinking\_zip/{f}")

def clean\_static\_folder():
folder = 'static'
if os.path.exists(folder):
for filename in os.listdir(folder):
filepath = os.path.join(folder, filename)
try:
if os.path.isfile(filepath) and not (
filepath.startswith(os.path.join(folder, "thinking")) or
filepath.startswith(os.path.join(folder, "thinking\_zip"))
):
os.remove(filepath)
except Exception as e:
print(f"Error deleting file {filename}: {e}")

clean\_static\_folder()

def extract\_zip\_or\_city(text):
zip\_match = re.search(r'\b77\d{3}\b', text)
if zip\_match:
return zip\_match.group(0)
for city in city\_to\_zip:
if city in text.lower():
return city\_to\_zip\[city]
return None

def get\_calendar\_zip\_matches(user\_zip, events):
matches = \[]
for event in events:
location = event.get('location', '')
match = re.search(r'\b77\d{3}\b', location)
if match and match.group(0) == user\_zip:
start = event\['start'].get('dateTime', event\['start'].get('date'))
matches.append(start)
return matches

def build\_zip\_prompt(user\_zip, matches):
if matches:
return f"We’ll already be in your area ({user\_zip}) at {', '.join(matches\[:2])}. Would one of those work for a free estimate?"
else:
return f"We’re not currently scheduled in {user\_zip}, but I can open up time for you. What day works best?"

def load\_credentials():
token\_json = os.environ.get("GOOGLE\_TOKEN")
if not token\_json:
print("❌ No GOOGLE\_TOKEN environment variable found.")
return None
try:
data = json.loads(token\_json)
return Credentials.from\_authorized\_user\_info(data, SCOPES)
except Exception as e:
print("❌ Failed to load credentials from GOOGLE\_TOKEN:", e)
return None

def load\_conversation(sid):
key = f"history:{sid}"
data = redis\_client.get(key)
return json.loads(data.decode()) if data else \[]

def save\_conversation(sid, history):
key = f"history:{sid}"
if len(history) > 7:
history = history\[:1] + history\[-6:]
redis\_client.set(key, json.dumps(history), ex=3600)

def clear\_conversation(sid):
key = f"history:{sid}"
redis\_client.delete(key)

def synthesize\_speech(text):
tts = requests.post(
f"[https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS\_VOICE\_ID}](https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID})",
headers={
"xi-api-key": ELEVENLABS\_API\_KEY,
"Content-Type": "application/json"
},
json={
"text": text,
"model\_id": "eleven\_multilingual\_v2",  # Change to 'eleven\_multilingual\_v2' or 'eleven\_monolingual\_v1' or Flash v2.5 if you have access
"voice\_settings": {
"stability": 0.5,
"similarity\_boost": 0.75,
"style": 0.4
}
}
)
if tts.status\_code != 200:
print("❌ ElevenLabs error:", tts.status\_code, tts.text)
return None
filename = f"{uuid.uuid4()}.mp3"
filepath = f"static/{filename}"
with open(filepath, "wb") as f:
f.write(tts.content)
return filename  # just the filename

@app.route("/test-openai", methods=\["GET"])
def test\_openai():
try:
client = openai.OpenAI(api\_key=OPENAI\_API\_KEY)
response = client.chat.completions.create(
model="gpt-4o",
messages=\[
{"role": "system", "content": "You are a helpful assistant."},
{"role": "user", "content": "Say hello!"}
]
)
return response.choices\[0].message.content
except Exception as e:
return f"❌ OpenAI API test failed: {e}"

@app.route("/static/[path\:filename](path:filename)")
def static\_files(filename):
return send\_from\_directory('static', filename)

@app.route("/response", methods=\["POST", "GET"])
def response\_route():
sid = request.values.get("sid")
if not sid:
return Response("<Response><Say>Missing session ID.</Say></Response>", mimetype="application/xml")

```
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
```

@app.route("/voice", methods=\["POST"])
def voice\_route():
\# This endpoint should handle incoming speech from Twilio's <Gather>
sid = request.values.get("sid")
user\_input = request.values.get("SpeechResult", "")
print(f"Received voice input: {user\_input}")

```
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
```

@app.route("/", methods=\["GET"])
def root():
return "Nick AI Voice Agent is running."

if **name** == "**main**":
port = int(os.environ.get("PORT", 5000))
print(f"🚀 Starting server on port {port}")
app.run(host="0.0.0.0", port=port)
