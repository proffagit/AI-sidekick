from transformers import pipeline
import torch
import json

# loading conf.json
with open('conf.json', 'r') as f:
    config = json.load(f)

ai1 = config["AI1"]

print("Welcome!", ai1, "is ready to assist you.")

