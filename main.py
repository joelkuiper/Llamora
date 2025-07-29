from flask import Flask, render_template, request
import requests


app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/messages")
def messages():
    messages = [{"text": "Test 1"},
                {"text": "Test 2"}]
    return render_template("partials/messages.html", messages=messages)
