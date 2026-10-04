# Jarvis

Assistente vocale personale **autonomo**: osserva le tue fonti (calendario, email, task, eventi custom), decide da solo se e come contattarti, e può **chiamarti al telefono** e parlare con te in italiano. Puoi anche chiamarlo tu.

## Architettura

```
                ┌──────────────────────────── server sempre acceso (VPS / Fly.io / Railway) ────────────────────────────┐
                │                                                                                                       │
 Fonti ───────► │  watchers/      ──eventi──►  triage (Haiku, economico)  ──►  policy.py (guardrail)  ──►  canale      │
 calendario     │  (polling o                  "serve disturbare Andrea?        quiet hours, max chiamate      ├─ push/Telegram (default)
 email, task    │   webhook)                    quanto è urgente?"              al giorno, dedup               └─ chiamata Twilio (solo urgente)
                │                                                                                                       │
 Telefono ◄───► │  Twilio ConversationRelay (STT+TTS gestiti) ◄──websocket──►  brain.py (Sonnet + tools)  ◄──► memory.py │
                │                                                                                                (SQLite) │
                └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Scelte chiave (e perché):**

- **Event-driven, non "LLM che pensa ogni 5 minuti".** I watcher producono eventi; Claude viene interpellato solo quando c'è qualcosa da valutare. Un heartbeat LLM continuo costa e genera chiamate inutili.
- **Due modelli.** Un modello economico fa il triage di ogni evento; quello più capace interviene solo durante la conversazione.
- **Escalation a gradini.** Notifica → chiamata. Un assistente che chiama troppo viene disattivato dopo una settimana: le chiamate sono riservate a ciò che è davvero urgente.
- **Twilio ConversationRelay** gestisce speech-to-text e text-to-speech: noi scambiamo solo testo via websocket. Meno codice e latenza accettabile. Se in futuro serve una voce custom, si passa a Media Streams + STT/TTS propri.
- **Guardrail in codice, non nel prompt.** Orari di silenzio, limite di chiamate e azioni consentite sono regole deterministiche in `policy.py`. Il modello non può aggirarle.

## Struttura

```
jarvis/
  main.py        FastAPI: webhook Twilio, websocket vocale, avvio scheduler
  brain.py       conversazione con Claude + tool
  triage.py      decide se/come contattare l'utente per un evento
  policy.py      guardrail deterministici
  caller.py      chiamate in uscita e notifiche
  scheduler.py   esegue i watcher periodicamente
  watchers/      sorgenti di eventi (esempio: promemoria)
  memory.py      SQLite: eventi, conversazioni, note
  config.py      impostazioni da .env
```

## Avvio in locale

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # compila le chiavi
uvicorn jarvis.main:app --reload --port 8000
# esponi la porta per Twilio: ngrok http 8000  → metti l'URL in PUBLIC_URL
```

In Twilio, imposta il webhook "A call comes in" del tuo numero su `POST {PUBLIC_URL}/twilio/voice`.

## Costi di esercizio (non coperti dai crediti di Claude Code)

| Voce | Cosa |
|---|---|
| API Anthropic | chiave da console.anthropic.com, fatturazione separata |
| Twilio | numero di telefono + minuti di chiamata + ConversationRelay |
| Hosting | VPS o PaaS sempre acceso |

## Roadmap

- [x] Scheletro: chiamata in entrata/uscita, conversazione, triage, guardrail
- [ ] Watcher Google Calendar
- [ ] Watcher Gmail (solo mittenti/parole chiave importanti)
- [ ] Canale Telegram per le notifiche non urgenti
- [ ] Tool di azione (con conferma vocale prima di qualsiasi azione irreversibile)
- [ ] Deploy + verifica firma webhook Twilio in produzione
