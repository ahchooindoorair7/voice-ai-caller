from flask import Flask, request, Response
import openai
import os

app = Flask(__name__)

# Initialize the OpenAI client
client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.route("/voice", methods=["POST"])
def voice():
    prompt = "You are Nick from AH-CHOO! Air Duct Cleaning. Greet the caller and offer a free estimate."

    try:
        # Call GPT-4 using the correct syntax for openai>=1.0.0
        chat_completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a friendly AI sales rep."},
                {"role": "user", "content": prompt}
            ]
        )

        # Extract reply from GPT-4
        reply = chat_completion.choices[0].message.content.strip()

        # Return TwiML voice response to Twilio
        return Response(f"""
        <Response>
            <Say voice="Polly.Matthew">{reply}</Say>
        </Response>
        """, mimetype="application/xml")

    except Exception as e:
        # Log the actual error for debugging
        print("ERROR from OpenAI:", e)
        return Response("""
        <Response>
            <Say>Sorry, something went wrong with our system. We'll call you back shortly. Thank you.</Say>
        </Response>
        """, mimetype="application/xml")

if __name__ == "__main__":
    # Use Render-assigned port
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

