"""Temporary: make a 16 kHz mono WAV with Polly to use as a fake microphone."""

import wave

import boto3
from dotenv import load_dotenv

load_dotenv()

TEXT = "Monty, what is the difference between Lambda and Fargate?"

polly = boto3.client("polly", region_name="us-east-1")
response = polly.synthesize_speech(
    Text=TEXT,
    VoiceId="Joanna",
    Engine="neural",
    OutputFormat="pcm",
    SampleRate="16000",
)
pcm = response["AudioStream"].read()

with wave.open("_fake-mic.wav", "wb") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(16000)
    out.writeframes(pcm)

print(f"_fake-mic.wav: {len(pcm)} bytes, {len(pcm) / 2 / 16000:.2f} s")
print("said:", TEXT)
