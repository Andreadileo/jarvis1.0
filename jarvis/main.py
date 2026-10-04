"""Server: webhook Twilio + websocket ConversationRelay + scheduler autonomo."""
import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, VoiceResponse

from . import caller, memory, scheduler
from .brain import Conversation
from .config import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched = scheduler.start()
    yield
    sched.shutdown()


app = FastAPI(title="Jarvis", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


async def verify_twilio(request: Request) -> None:
    s = get_settings()
    if s.twilio_skip_signature_check:
        return
    url = s.public_url + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    form = dict(await request.form())
    sig = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(s.twilio_auth_token).validate(url, form, sig):
        raise HTTPException(status_code=403, detail="Firma Twilio non valida")


@app.post("/twilio/voice")
async def voice(request: Request, reason: str | None = None):
    await verify_twilio(request)
    s = get_settings()
    ws_url = s.public_url.replace("https://", "wss://").replace("http://", "ws://") + "/twilio/relay"
    if reason:
        greeting = "Ciao Andrea, sono Jarvis. Ti chiamo per una cosa."
    else:
        memory.log_call("in", "")
        greeting = "Ciao Andrea, dimmi pure."
    resp = VoiceResponse()
    connect = Connect()
    relay = connect.conversation_relay(
        url=ws_url,
        language=s.language,
        tts_provider=s.tts_provider,
        voice=s.tts_voice,
        welcome_greeting=greeting,
    )
    if reason:
        relay.parameter(name="reason", value=reason)
    resp.append(connect)
    return Response(content=str(resp), media_type="application/xml")


@app.post("/debug/call")
def debug_call(x_debug_token: str = Header(default="")):
    s = get_settings()
    if not s.debug_token or not secrets.compare_digest(x_debug_token, s.debug_token):
        raise HTTPException(status_code=403)
    sid = caller.place_call("Chiamata di prova: sto verificando che tutto funzioni.")
    if not sid:
        raise HTTPException(status_code=503, detail="Twilio non configurato")
    return {"call_sid": sid}


def _text(token: str, last: bool) -> str:
    return json.dumps({"type": "text", "token": token, "last": last})


@app.websocket("/twilio/relay")
async def relay(ws: WebSocket):
    await ws.accept()
    convo: Conversation | None = None
    task: asyncio.Task | None = None
    sent = ""

    async def speak(user_text: str) -> None:
        nonlocal sent
        sent, pending = "", None
        async for token in convo.stream(user_text):
            if pending is not None:
                await ws.send_text(_text(pending, last=False))
            pending = token
            sent += token
        await ws.send_text(_text(pending or "", last=True))

    async def stop(spoken: str | None = None) -> None:
        if task and not task.done():
            task.cancel()
            await asyncio.wait([task])
            convo.interrupted(spoken or sent)

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "setup":
                reason = (msg.get("customParameters") or {}).get("reason")
                convo = Conversation(reason=reason)
                log.info("relay setup callSid=%s reason=%s", msg.get("callSid"), reason)
                if reason:
                    task = asyncio.create_task(speak("(Andrea ha risposto alla tua chiamata. Spiegagli il motivo.)"))
            elif convo is None:
                continue
            elif kind == "prompt" and msg.get("last", True):
                await stop()
                task = asyncio.create_task(speak(msg.get("voicePrompt", "")))
            elif kind == "interrupt":
                await stop(msg.get("utteranceUntilInterrupt"))
            elif kind == "error":
                log.error("relay error: %s", msg.get("description"))
    except WebSocketDisconnect:
        pass
    finally:
        if task and not task.done():
            task.cancel()
