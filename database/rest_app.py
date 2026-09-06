#!/usr/bin/env python3
from flask import Flask, jsonify
from pymongo import MongoClient

app = Flask(__name__)
client = MongoClient("localhost", 27017)
collection = client["ca0"]["flows"]

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/alerts", methods=["GET"])
def get_alerts():
    alerts = list(collection.find({"Label": {"$ne": "BENIGN"}}, {"_id": 0}).limit(50))
    return jsonify({"count": len(alerts), "alerts": alerts})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
