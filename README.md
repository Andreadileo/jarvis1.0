# Jarvis

Assistente vocale personale **autonomo**: osserva le tue fonti (calendario, email, task, eventi custom), decide da solo se e come contattarti, e **ti parla**: dalle casse del PC se sei lì e libero, su Telegram (testo + nota vocale) se sei lontano. La telefonata Twilio è opzionale e serve solo per le urgenze quando non sei al PC.

**Costo di esercizio con la configurazione base: solo l'API Anthropic** (centesimi al giorno con Haiku sul triage). Telegram, la voce locale (Windows SAPI o Piper) e il rilevamento presenza sono gratuiti.

## Architettura

```
                ┌──────────────────────── il tuo PC Windows (o un server sempre acceso) ─────────────────────────────┐
                │                                                                                                    │
 Fonti ───────► │  watchers/   ──eventi──►  triage (Haiku)   ──►  policy.py (guardrail)     ──►  canale             │
 calendario     │  (polling o               "quanto è urgente?"    quiet hours, max chiamate,      ├─ voce locale (al PC e libero)
 email, task    │   webhook)                                       presenza al PC, dedup           ├─ Telegram testo + vocale (lontano/occupato)
                │                                   ▲                                              └─ Twilio (opzionale, solo urgente)
                │                        presence.py: inattività, mic in uso, schermo intero                        │
                │                                                                                                    │
 Telefono ◄───► │  Twilio ConversationRelay (opzionale) ◄──websocket──►  brain.py (Sonnet + tools)  ◄──► memory.py  │
                │                                                                                          (SQLite) │
                └────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Scelte chiave (e perché):**

- **Event-driven, non "LLM che pensa ogni 5 minuti".** I watcher producono eventi; Claude viene interpellato solo quando c'è qualcosa da valutare. Un heartbeat LLM continuo costa e genera chiamate inutili.
- **Due modelli.** Un modello economico fa il triage di ogni evento; quello più capace interviene solo durante la conversazione.
- **Escalation a gradini.** Log → Telegram → voce al PC → chiamata. Un assistente che interrompe troppo viene disattivato dopo una settimana.
- **La presenza decide il canale, non il modello.** Se sei attivo al PC e nessuna app usa il microfono, Jarvis parla. Se sei in call, a schermo intero o in "non disturbare", ti scrive su Telegram. Se sei lontano, Telegram con nota vocale; solo le urgenze alte, e solo se Twilio è configurato, diventano una telefonata.
- **Voce gratuita.** La sintesi è locale: voce italiana di Windows senza installare nulla, oppure Piper se vuoi una voce migliore. Twilio resta solo per la telefonata vera.
- **Guardrail in codice, non nel prompt.** Orari di silenzio, limite di chiamate e azioni consentite sono regole deterministiche in `policy.py`. Il modello non può aggirarle.

## Struttura

```
jarvis/
  main.py        FastAPI: webhook Twilio, websocket vocale, avvio scheduler
  brain.py       conversazione con Claude + tool
  triage.py      decide se/come contattare l'utente per un evento
  policy.py      guardrail deterministici
  caller.py      instrada verso voce locale / Telegram / Twilio
  presence.py    sei al PC? sei occupato? (Windows: input, microfono, schermo intero)
  speech.py      sintesi vocale locale (Windows SAPI o Piper) + conversione per Telegram
  channels/      telegram.py: testo e note vocali
  scheduler.py   esegue i watcher periodicamente
  watchers/      sorgenti di eventi (esempio: promemoria)
  memory.py      SQLite: eventi, conversazioni, note
  config.py      impostazioni da .env
```

## Avvio in locale

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # minimo: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
uvicorn jarvis.main:app --reload --port 8000
```

Telegram: crea un bot con @BotFather, scrivigli "ciao", poi apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia `chat.id`.
Note vocali su Telegram: serve `ffmpeg` nel PATH (`winget install ffmpeg`). Senza, arriva solo il testo.

Solo se vuoi la telefonata: compila le variabili Twilio, esponi la porta (`ngrok http 8000` → `PUBLIC_URL`) e imposta il webhook "A call comes in" su `POST {PUBLIC_URL}/twilio/voice`.

## Costi di esercizio (non coperti dai crediti di Claude Code)

| Voce | Cosa | Obbligatorio? |
|---|---|---|
| API Anthropic | chiave da console.anthropic.com, fatturazione separata | sì |
| Telegram, voce locale, presenza | gratuiti | — |
| Twilio | numero + minuti + ConversationRelay | no, solo per la telefonata |
| Hosting | il tuo PC va bene; Jarvis dorme quando dorme il PC | no |

## Roadmap

- [x] Scheletro: chiamata in entrata/uscita, conversazione, triage, guardrail
- [ ] Watcher Google Calendar
- [ ] Watcher Gmail (solo mittenti/parole chiave importanti)
- [x] Canale Telegram (testo + nota vocale) e voce locale al PC in base alla presenza
- [ ] Ingresso vocale al PC (wake word + STT locale) riusando `brain.py`
- [ ] Watcher trascrizioni riunioni (Teams/Meet) da cartella locale
- [ ] Tool di azione (con conferma vocale prima di qualsiasi azione irreversibile)
- [ ] Deploy + verifica firma webhook Twilio in produzione
