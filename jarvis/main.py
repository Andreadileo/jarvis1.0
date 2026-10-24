"""Server: webhook Twilio, websocket ConversationRelay, inbox HTTP, scheduler autonomo."""
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.request_validator import RequestValidator

from . import agent, memory, scheduler
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


async def _check_twilio_signature(request: Request) -> None:
    """Chi non è Twilio non deve poter far squillare (o far parlare) Jarvis."""
    s = get_settings()
    if not s.verify_twilio_signature:
        return
    if not s.twilio_auth_token:
        log.warning("TWILIO_AUTH_TOKEN assente: verifica della firma saltata")
        return
    form = await request.form()
    url = s.public_url.rstrip("/") + request.url.path
    if request.url.query:
        url += f"?{request.url.query}"
    validator = RequestValidator(s.twilio_auth_token)
    if not validator.validate(url, dict(form), request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(status_code=403, detail="firma Twilio non valida")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    """Cosa ha fatto Jarvis nelle ultime 24 ore."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).isoformat()
    events = memory.events_since(since)
    by_action: dict[str, int] = {}
    for e in events:
        by_action[e["decision"]] = by_action.get(e["decision"], 0) + 1
    return {
        "events_24h": len(events),
        "decisions": by_action,
        "calls_24h": memory.outbound_calls_since(since),
        "notifications_1h": memory.notifications_since((now - timedelta(hours=1)).isoformat()),
        "open_commitments": len(memory.open_commitments(limit=100)),
        "pending_retries": len(memory.due_pending((now + timedelta(days=365)).isoformat())),
        "last_briefing": memory.job_last_run(agent.JOB),
    }


@app.post("/events")
async def push_event(request: Request, x_jarvis_token: str = Header(default="")):
    """Ingresso unico per fonti esterne: Shortcut iOS, IFTTT, Zapier, webhook vari.

    Body: {"id": "...", "source": "...", "title": "...", "body": "..."}
    """
    s = get_settings()
    if not s.inbound_token or x_jarvis_token != s.inbound_token:
        raise HTTPException(status_code=401, detail="token mancante o errato")
    data = await request.json()
    title = str(data.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=422, detail="title obbligatorio")
    source = str(data.get("source", "esterno"))
    event_id = str(data.get("id") or f"{source}:{datetime.now(timezone.utc).timestamp()}")
    memory.push_inbox(f"ext:{event_id}", source, title, str(data.get("body", "")))
    return {"queued": True, "id": f"ext:{event_id}"}


@app.post("/briefing")
async def briefing_now(x_jarvis_token: str = Header(default="")):
    s = get_settings()
    if not s.inbound_token or x_jarvis_token != s.inbound_token:
        raise HTTPException(status_code=401, detail="token mancante o errato")
    return {"text": agent.run_briefing()}


@app.post("/twilio/voice")
async def voice(request: Request, reason: str | None = None, event_id: str = ""):
    await _check_twilio_signature(request)
    s = get_settings()
    ws_url = s.public_url.replace("https://", "wss://").replace("http://", "ws://") + "/twilio/relay"
    params = []
    if reason:
        params.append(f"reason={quote(reason)}")
        greeting = "Ciao Andrea, sono Jarvis. Ti chiamo per una cosa."
    else:
        memory.log_call("in", "")
        greeting = "Ciao Andrea, dimmi pure."
    if event_id:
        params.append(f"event_id={quote(event_id)}")
    if params:
        ws_url += "?" + "&".join(params)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        f"<ConversationRelay url={quoteattr(ws_url)} language={quoteattr(s.language)} "
        f"welcomeGreeting={quoteattr(greeting)} />"
        "</Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/status")
async def call_status(request: Request):
    """Esito della chiamata: una non risposta è un segnale, non un buco nero."""
    await _check_twilio_signature(request)
    form = await request.form()
    sid, status_value = form.get("CallSid", ""), form.get("CallStatus", "")
    if not sid:
        return {"ok": False}
    memory.set_call_status(sid, status_value)
    call = memory.call_by_sid(sid)
    if call and call.get("event_id") and status_value in ("no-answer", "busy", "failed"):
        # non ha risposto: il motivo non si perde, resta per il briefing
        memory.queue_digest(f"(chiamata senza risposta) {call['reason']}")
    return {"ok": True}


@app.websocket("/twilio/relay")
async def relay(ws: WebSocket):
    await ws.accept()
    reason = ws.query_params.get("reason")
    event_id = ws.query_params.get("event_id", "")
    convo = Conversation(reason=reason, event_id=event_id)
    try:
        if reason:  # chiamata in uscita: Jarvis apre con il motivo
            opening = await convo.reply("(Andrea ha risposto alla tua chiamata. Spiegagli il motivo.)")
            await ws.send_text(json.dumps({"type": "text", "token": opening, "last": True}))
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "setup":
                convo.call_sid = msg.get("callSid", "")
            elif kind == "prompt" and msg.get("last", True):
                answer = await convo.reply(msg.get("voicePrompt", ""))
                await ws.send_text(json.dumps({"type": "text", "token": answer, "last": True}))
            elif kind == "error":
                log.warning("ConversationRelay: %s", msg.get("description"))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("conversazione interrotta da un errore")
