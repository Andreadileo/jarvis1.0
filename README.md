# Jarvis

Assistente vocale personale **autonomo**: osserva le tue fonti (calendario, email, eventi esterni), decide da solo se e come contattarti, e può **chiamarti al telefono** e parlare con te in italiano. Puoi anche chiamarlo tu.

## Architettura

```
                ┌──────────────────────── server sempre acceso (VPS / Fly.io / Railway) ────────────────────────┐
                │                                                                                               │
 calendario ──► │  watchers/ ──eventi──► triage (Haiku + storico) ──► policy.py ──► azione                      │
 email IMAP     │                        "serve disturbare?"          guardrail      ignora / registra          │
 impegni        │                                                     deterministici  digest / notifica / chiama │
 POST /events   │                                                          │                   │                │
                │                                                          │                   ├─ Telegram      │
                │                                                          │                   └─ Twilio        │
                │                                              chiamata rimandata                               │
                │                                              (quiet hours) ──► riprovata da sola               │
                │                                                                                               │
 Telefono ◄───► │  Twilio ConversationRelay ◄──websocket──► brain.py (Sonnet + tool) ◄──► memory.py (SQLite)    │
                │                                                   │                                           │
                │                                                   └─ "non era importante" ──► feedback ──┐    │
                │                                                                                          │    │
                │  agent.py: briefing del mattino (1 volta al giorno) ◄── digest + impegni + eventi        │    │
                │                                                                                          ▼    │
                │                                                                      il triage si ricalibra    │
                └───────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Scelte chiave (e perché):**

- **Event-driven, non "LLM che pensa ogni 5 minuti".** I watcher producono eventi; Claude viene interpellato solo quando c'è qualcosa da valutare. L'unica eccezione è il briefing quotidiano: una volta al giorno, a costo noto.
- **Due modelli.** Un modello economico fa il triage di ogni evento; quello più capace interviene durante la conversazione e per scrivere il briefing.
- **Escalation a gradini.** `ignore → log → digest → notifica → chiamata`. Un assistente che chiama troppo viene disattivato dopo una settimana: le chiamate sono riservate a ciò che è davvero urgente.
- **Guardrail in codice, non nel prompt.** Orari di silenzio, limite di chiamate, budget di notifiche orarie e regole dell'utente sono deterministici in `policy.py`. Il modello non può aggirarli parlando.
- **Niente si perde in silenzio.** Una chiamata bloccata dalle quiet hours viene *rimandata*, non buttata; una chiamata senza risposta finisce nel briefing; ciò che sta sotto soglia si accumula nel digest.
- **L'autonomia richiede un freno a mano.** Ogni tool che agisce verso l'esterno o cancella qualcosa pretende un sì esplicito a voce, verificato in codice (`CONFIRM_REQUIRED` in `brain.py`), non nel prompt.
- **Twilio ConversationRelay** gestisce speech-to-text e text-to-speech: noi scambiamo solo testo via websocket.

## Il ciclo di apprendimento

Il pezzo che distingue un assistente autonomo da uno rumoroso:

1. Jarvis ti chiama. Tu dici *"guarda, non era importante"*.
2. Il modello chiama il tool `rate_event` → il giudizio finisce in `feedback`.
3. Al triage successivo il prompt contiene *"Storico 'email': 4 valutazioni di troppo contro 1 azzeccata. Abbassa."*
4. Se invece dici *"non disturbarmi più per le newsletter"*, nasce una **regola** in `rules`: non è una preferenza suggerita al modello, è un filtro deterministico applicato in `policy.py`.

Stessa cosa al contrario: `prioritize_topic` rende un argomento sempre degno di una telefonata, sempre entro i guardrail (di notte resta una notifica).

## Fonti di eventi

| Watcher | Come si attiva | Note |
|---|---|---|
| `reminders.py` | file `reminders.json` | esempio, utile per provare |
| `commitments.py` | automatico | i "ricordamelo domani" presi a voce tornano a galla da soli |
| `calendar_ics.py` | `CALENDAR_ICS_URLS` | URL iCal privato (Google: Impostazioni → Indirizzo segreto in formato iCal). Niente OAuth. Le RRULE non vengono espanse: Google e Apple pubblicano già le occorrenze |
| `email_imap.py` | `IMAP_*` | sola lettura (`BODY.PEEK`, non marca le email come lette), solo mittenti VIP o parole chiave, max 20 messaggi per giro |
| `inbox.py` | `POST /events` | ingresso unico per Shortcut iOS, IFTTT, Zapier, webhook GitHub, sensori di casa |

Un watcher spento se non configurato è il comportamento voluto: senza filtri espliciti Jarvis resta muto.

```bash
curl -X POST $PUBLIC_URL/events \
  -H "X-Jarvis-Token: $INBOUND_TOKEN" \
  -d '{"id":"casa-1","source":"casa","title":"Allarme attivato","body":"porta di servizio"}'
```

## API

| Endpoint | Cosa fa |
|---|---|
| `GET /health` | liveness |
| `GET /status` | cosa ha fatto Jarvis nelle ultime 24 ore (eventi, decisioni, chiamate, impegni aperti) |
| `POST /events` | inbox esterna (header `X-Jarvis-Token`) |
| `POST /briefing` | forza il briefing adesso (header `X-Jarvis-Token`) |
| `POST /twilio/voice` | webhook chiamata (firma verificata) |
| `POST /twilio/status` | esito della chiamata: una non risposta diventa una voce del briefing |
| `WS /twilio/relay` | conversazione vocale |

## Struttura

```
jarvis/
  main.py        FastAPI: webhook Twilio, websocket vocale, inbox HTTP, avvio scheduler
  brain.py       conversazione con Claude + tool (con conferma obbligatoria per le azioni esterne)
  triage.py      valuta l'urgenza di un evento, con il contesto appreso
  policy.py      guardrail deterministici: soglie, quiet hours, regole, rinvii
  agent.py       iniziativa propria: briefing quotidiano
  caller.py      chiamate Twilio e notifiche Telegram
  scheduler.py   orchestrazione: watcher -> triage -> policy -> canale
  watchers/      sorgenti di eventi
  memory.py      SQLite: eventi, chiamate, note, impegni, regole, feedback, digest
  dates.py       date in italiano senza dipendere dal locale
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
In locale, con un `PUBLIC_URL` che cambia a ogni avvio di ngrok, puoi mettere `VERIFY_TWILIO_SIGNATURE=false`; in produzione lascialo `true`.

Test: `python -m pytest -q`

## Costi di esercizio (non coperti dai crediti di Claude Code)

| Voce | Cosa |
|---|---|
| API Anthropic | chiave da console.anthropic.com, fatturazione separata |
| Twilio | numero di telefono + minuti di chiamata + ConversationRelay |
| Hosting | VPS o PaaS sempre acceso |

Il triage gira su ogni evento nuovo: con fonti rumorose (una casella email senza filtri) è lì che si spende. I filtri VIP/parole chiave sono anche una misura di costo, non solo di attenzione.

## Roadmap

- [x] Scheletro: chiamata in entrata/uscita, conversazione, triage, guardrail
- [x] Watcher calendario (feed iCal)
- [x] Watcher email (IMAP, solo mittenti e parole chiave importanti)
- [x] Canale Telegram per le notifiche non urgenti
- [x] Inbox HTTP per fonti esterne arbitrarie
- [x] Impegni persistenti e rinvio automatico delle chiamate bloccate
- [x] Ciclo di feedback: regole esplicite e ricalibrazione del triage
- [x] Briefing quotidiano
- [x] Tool di azione con conferma vocale obbligatoria
- [x] Verifica della firma dei webhook Twilio
- [ ] Feedback anche da Telegram (oggi si dà solo a voce)
- [ ] Watcher Google Calendar via OAuth, per *scrivere* sull'agenda
- [ ] Deploy: Dockerfile e healthcheck
