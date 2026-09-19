#!/usr/bin/env python3

from flask import Flask, jsonify

from pymongo import MongoClient


app = Flask(__name__)


# Connect to MongoDB running on this same VM
client = MongoClient("localhost", 27017)

# Use the "ca0" database and the "flows" collection
# This is where our processor stored the network-flow records
collection = client["ca0"]["flows"]


# Create a GET endpoint at /health
@app.route("/health", methods=["GET"])
def health():

    # Return a simple JSON response
    return jsonify({"status": "ok"})


# Create a GET endpoint at /alerts to retrieve attack flows stored in MongoDB
@app.route("/alerts", methods=["GET"])
def get_alerts():

    # Find records where the Label is NOT BENIGN
    # {"_id": 0} leaves MongoDB's internal _id field out of the response
    # limit(50) returns at most 50 records
    alerts = list(
        collection.find(
            {"Label": {"$ne": "BENIGN"}},
            {"_id": 0}
        ).limit(50)
    )

    # Return the alerts as JSON
    return jsonify({
        "count": len(alerts),
        "alerts": alerts
    })


if __name__ == "__main__":

    app.run(host="0.0.0.0", port=8080)
