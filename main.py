from flask import Flask, Response

app = Flask(__name__)

@app.route("/response", methods=["GET", "POST"])
def response_route():
    print(">>> IN RESPONSE ROUTE <<<")    # This should show in logs!
    assert False, "Crash!"
    return Response("Should never get here.", mimetype="text/plain")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
