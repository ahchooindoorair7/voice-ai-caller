from flask import Flask, request, Response, send_file
import openai
import os
import uuid
import requests

app = Flask(__name__)

# Load keys
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = "sk_28e9af6168313208486554115e37bd8f06db2c2bf68f75f2"
ELEVENLABS_VOICE_ID = "a73940a2-08f2-4db8-9ebc-63f01eacbe89"  # ✅ Your real voice ID

# Create OpenAI client
client = openai.OpenAI(api_key=OPENAI_API_KEY)

@app.before_request
def log_request_info():
    print("📥 Incoming request:", request.method, request.path, flush=True)

@app.route("/", methods=["GET"])
def root():
    return "Nick AI Voice Agent is running."

@app.route("/voice", methods=["POST"])
def voice():
    print("🟢 Twilio POST /voice received", flush=True)

    prompt = "You are Nick from AH-CHOO! Indoor Air Quality Specialists. Greet the caller and offer a free estimate."

    try:
        # Generate reply from GPT-4o
        print("📡 Sending prompt to GPT-4o...", flush=True)
        chat_completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a friendly, upbeat sales rep."},
                {"role": "user", "content": prompt}
            ]
        )

        reply = chat_completion.choices[0].message.content.strip()
        print("✅ GPT-4o replied:", reply, flush=True)

        # Generate audio using ElevenLabs
        print("🎙️ Sending text to ElevenLabs...", flush=True)
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
            print("❌ ElevenLabs Error:", response.text, flush=True)
            raise Exception("Failed to generate audio")

        # Save audio to temp file
        filename = f"{uuid.uuid4()}.mp3"
        filepath = f"static/{filename}"
        with open(filepath, "wb") as f:
            f.write(response.content)
        print("🎧 Audio saved to:", filepath, flush=True)

        # Return TwiML to play the audio
        return Response(f"""
        <Response>
            <Play>https://{request.host}/static/{filename}</Play>
        </Response>
        """, mimetype="application/xml")

    except Exception as e:
        print("❌ ERROR:", e, flush=True)
        return Response("""
        <Response>
            <Say>Sorry, something went wrong. We'll call you back shortly.</Say>
        </Response>
        """, mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}", flush=True)

    # Make sure static folder exists
    if not os.path.exists("static"):
        os.makedirs("static")

    app.run(host="0.0.0.0", port=port)



