# Istruzioni per Claude (sessioni cloud)

Progetto: Jarvis, assistente vocale autonomo in Python (FastAPI + Anthropic SDK + Twilio). Vedi README.md per l'architettura.

## Regole
- Lingua dell'utente e dei testi parlati: italiano. Codice, nomi e commenti tecnici: inglese va bene.
- I guardrail stanno in `jarvis/policy.py` e sono deterministici: non spostarli nei prompt e non indebolirli senza una richiesta esplicita.
- Qualsiasi tool che compie azioni verso l'esterno (inviare email, pagare, cancellare) deve richiedere conferma esplicita dell'utente durante la chiamata.
- Mai committare segreti: tutto passa da `.env` (vedi `.env.example`).
- Modelli: triage su `TRIAGE_MODEL` (economico), conversazione su `CHAT_MODEL`. Non hardcodare gli ID dei modelli.
- Ogni nuovo watcher va in `jarvis/watchers/`, restituisce oggetti `Event` e va registrato in `scheduler.py`.
- Prima di dire che un lavoro è finito: `python -m pytest -q` deve passare.

## Comandi
- Avvio: `uvicorn jarvis.main:app --reload --port 8000`
- Test: `python -m pytest -q`

## Skill di progetto
- In `.claude/skills/` ci sono `caveman` (risposte terse) e `ponytail` (soluzione minima che funziona). Si attivano con `/caveman` e `/ponytail`. Vedi `.claude/skills/README.md` per origine e licenze.
