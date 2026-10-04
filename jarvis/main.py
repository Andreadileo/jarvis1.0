"""Server: webhook Twilio + websocket ConversationRelay + scheduler autonomo."""
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from . import memory, scheduler
from .brain import Conversation
from .config import get_settings

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched = scheduler.start()
    yield
    sched.shutdown()


app = FastAPI(title="Jarvis", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/twilio/voice")
async def voice(request: Request, reason: str | None = None):
    # TODO produzione: verificare la firma X-Twilio-Signature (twilio.request_validator).
    s = get_settings()
    ws_url = s.public_url.replace("https://", "wss://").replace("http://", "ws://") + "/twilio/relay"
    if reason:
        ws_url += f"?reason={quote(reason)}"
        greeting = "Ciao Andrea, sono Jarvis. Ti chiamo per una cosa."
    else:
        memory.log_call("in", "")
        greeting = "Ciao Andrea, dimmi pure."
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        f"<ConversationRelay url={quoteattr(ws_url)} language={quoteattr(s.language)} "
        f"welcomeGreeting={quoteattr(greeting)} />"
        "</Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/twilio/relay")
async def relay(ws: WebSocket):
    await ws.accept()
    reason = ws.query_params.get("reason")
    convo = Conversation(reason=reason)
    if reason:  # chiamata in uscita: Jarvis apre con il motivo
        opening = await convo.reply("(Andrea ha risposto alla tua chiamata. Spiegagli il motivo.)")
        await ws.send_text(json.dumps({"type": "text", "token": opening, "last": True}))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "prompt" and msg.get("last", True):
                answer = await convo.reply(msg.get("voicePrompt", ""))
                await ws.send_text(json.dumps({"type": "text", "token": answer, "last": True}))
    except WebSocketDisconnect:
        pass
