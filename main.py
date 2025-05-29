import os
import uuid
import json
import datetime
import requests
import openai
import re
import redis
import logging
from flask import Flask, request, Response, send_from_directory, redirect
from flask_session import Session
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

print("THIS IS THE VERY TOP OF MAIN.PY (should appear in logs immediately)", flush=True)
logging.basicConfig(level=logging.INFO)
logging.info("LOGGING WORKS AT THE TOP OF MAIN.PY")

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

@app.route("/response", methods=["POST", "GET"])
def response_route():
    print("ENTER /response_route (assert fail test)", flush=True)
    logging.info("ENTER /response_route (assert fail test)")
    assert False, "THIS SHOULD CRASH AND SHOW A TRACEBACK"
    return "Should not get here"

@app.route("/", methods=["GET"])
def root():
    return "Crash test app is running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port)
