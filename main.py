from flask import Flask, request, Response
import openai
import os

app = Flask(__name__)

openai.api_key = os.environ["OPENAI_API_KEY"]

@app.route("/voice", methods=["POST"])
def voice():
    prompt = "You are Nick from AH-CHOO! Air Duct Cleaning. Politely greet the customer and offer a free estimate."

    gpt_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a friendly AI sales rep."},
            {"role": "user", "content": prompt}
        ]
    )

    reply = gpt_response["choices"][0]["message"]["content"]

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Matthew">{reply}</Say>
</Response>"""

    return Response(twiml_response, mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
