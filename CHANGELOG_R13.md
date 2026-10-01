# R13 · Supabase Performance / Bulk Publishing

## Obiettivo
Ridurre i round-trip PostgreSQL/Supabase durante anteprima, pubblicazione Buyer e generazione delta ERP, senza cambiare le regole funzionali R10/R11/R12.

## Modifiche principali

- Bootstrap PostgreSQL e migrazioni Master22 eseguiti una sola volta per processo Python.
- Anteprima pubblicazione: caricamento bulk di prodotti e offerte con `ANY(...)`, eliminando le due query per ogni riga.
- Pubblicazione PostgreSQL:
  - UPSERT bulk prodotti con `INSERT ... ON CONFLICT DO UPDATE`;
  - UPSERT bulk offerte;
  - INSERT bulk dello storico prezzi;
  - INSERT bulk delle righe di pubblicazione;
  - una sola transazione per il nucleo della pubblicazione.
- Campi Master22 `Forma Farmaceutica`, `X`, `Y`, `Z` aggiornati in un'unica operazione `UPDATE ... FROM (VALUES ...)`.
- Il preview Master22 legge i campi extra soltanto per i prodotti presenti nel file corrente.
- Delta ERP Buyer:
  - lookup bulk di `product_id` e `offer_id`;
  - INSERT bulk degli eventi ERP;
  - idempotenza invariata tramite `event_key` e `ON CONFLICT DO NOTHING`.
- Logging prestazionale `[PERF]` per le principali fasi di pubblicazione.

## Compatibilità funzionale

Restano invariati:

- Batch ID e controllo dataset già pubblicato;
- classificazione `NUOVO PRODOTTO`, `NUOVA OFFERTA`, `PREZZO MODIFICATO`, `DATI MODIFICATI`, `PREZZO + ANAGRAFICA`, `INVARIATO`;
- storico prezzi;
- R11 `VISIBLE / HIDDEN / ARCHIVED`;
- coda ERP R12 `INSERT / UPDATE / DISABLE / REACTIVATE`;
- export e consultazione listino;
- backend SQLite per sviluppo locale.

## Benchmark consigliato dopo il deploy

Eseguire, in ordine:

1. file piccolo (5-20 righe) per smoke test;
2. file equivalente al test reale da circa 428 righe;
3. file da circa 2.000 righe;
4. se stabile, prova da 5.000 righe.

Per ogni prova annotare:

- tempo anteprima pubblicazione;
- tempo dal click `Pubblica a listino` alla conferma;
- eventuali timeout PostgreSQL;
- righe/batch effettivamente pubblicati;
- numero di eventi ERP PENDING generati.

## Nota infrastrutturale

R13 non aumenta il `statement_timeout` di Supabase: riduce il lavoro e il numero di statement necessari. L'eventuale migrazione a DigitalOcean resta una valutazione successiva, principalmente per CPU/RAM della validazione Python e concorrenza utenti.
