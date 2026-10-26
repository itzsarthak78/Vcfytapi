
import os
import hmac
import asyncio
import subprocess
import tempfile
from pathlib import Path

import httpx
import imageio_ffmpeg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Rural Voice FX")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "")


class VoiceRequest(BaseModel):
    chat_id: int
    file_id: str = Field(min_length=1, max_length=1024)


@app.get("/")
async def health():
    return {"status": "online", "service": "Rural Voice FX"}


@app.post("/")
async def process_voice(
    body: VoiceRequest,
    x_api_key: str = Header(default="")
):
    if not BACKEND_API_KEY or not hmac.compare_digest(
        x_api_key, BACKEND_API_KEY
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Bot not configured")

    telegram_api = f"https://api.telegram.org/bot{BOT_TOKEN}"

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            f"{telegram_api}/getFile",
            json={"file_id": body.file_id}
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            raise HTTPException(400, "Telegram file unavailable")

        file_path = data["result"]["file_path"]

        if not file_path.endswith((".oga", ".ogg")):
            raise HTTPException(400, "Send a Telegram voice note")

        download = await client.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        )
        download.raise_for_status()

        if len(download.content) > 20 * 1024 * 1024:
            raise HTTPException(413, "Audio exceeds 20 MB")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.ogg"
            output = Path(tmp) / "output.ogg"
            source.write_bytes(download.content)

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(source),
                "-af",
                "bass=g=7:f=100,"
                "volume=15dB,"
                "aecho=0.8:0.20:60|120:0.25|0.15,"
                "alimiter=limit=0.95",
                "-c:a", "libopus", "-b:a", "64k",
                str(output)
            ]

            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    capture_output=True,
                    timeout=30
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(504, "Processing timed out")

            if proc.returncode != 0 or not output.exists():
                raise HTTPException(500, "Audio processing failed")

            with output.open("rb") as audio:
                sent = await client.post(
                    f"{telegram_api}/sendVoice",
                    data={
                        "chat_id": body.chat_id,
                        "caption": "🎙️ RURAL VOICE FX"
                    },
                    files={
                        "voice": ("voice.ogg", audio, "audio/ogg")
                    },
                    timeout=30
                )
                sent.raise_for_status()

            if not sent.json().get("ok"):
                raise HTTPException(502, "Could not send processed voice")

    return {"ok": True, "message": "Processed voice sent"}
