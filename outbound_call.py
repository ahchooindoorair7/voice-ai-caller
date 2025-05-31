import os
from twilio.rest import Client

account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
client = Client(account_sid, auth_token)

call = client.calls.create(
    to='+19854009790',            # number to call (test with your own phone)
    from_='+18329815087',           # your Twilio number
    url='https://voice-ai-caller.onrender.com/voice-greeting',  # your deployed Flask endpoint
    machine_detection='Enable'
)

print("Call started. Call SID:", call.sid)
