from flask import Flask, request, Response
import openai
import os

app = Flask(__name__)

@app.before_request
def log_request_info():
    print("📥 Incoming request:", request.method, request.path, flush=True)

@app.route("/", methods=["GET", "POST"])
def root():
    print("🏠 Hit root route!", flush=True)
    return "Voice AI is running."

@app.route("/voice", methods=["POST"])
def voice():
    print("🟢 Twilio POST /voice received", flush=True)

    prompt = "You are Nick from AH-CHOO! Air Duct Cleaning. Greet the caller and offer a free estimate."

    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        print("🔐 API key loaded:", bool(api_key), flush=True)

        client = openai.OpenAI(api_key=api_key)

        print("📡 Sending prompt to GPT-4.1 (gpt-4o)...", flush=True)
        chat_completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a friendly AI sales rep."},
                {"role": "user", "content": prompt}
            ]
        )

        reply = chat_completion.choices[0].message.content.strip()
        print("✅ GPT-4o replied:", reply, flush=True)

        return Response(f"""
        <Response>
            <Say voice="Polly.Matthew">{reply}</Say>
        </Response>
        """, mimetype="application/xml")

    except Exception as e:
        print("❌ ERROR from OpenAI:", e, flush=True)
        return Response("""
        <Response>
            <Say>Sorry, something went wrong with our system. We'll call you back shortly. Thank you.</Say>
        </Response>
        """, mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)



