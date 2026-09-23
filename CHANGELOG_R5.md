# Wave 2 Demo R5 — Assistente LLM locale

## Novità

- integrazione opzionale con Ollama via endpoint locale;
- selezione automatica dei modelli Ollama installati;
- LLM usato solo per estrarre intento e filtri, mai per generare/eseguire SQL;
- nessuna riga del listino viene inviata al modello;
- grounding dei valori estratti contro il database reale;
- fornitore/principio attivo inesistente => richiesta bloccata;
- export senza filtri => bloccato salvo richiesta esplicita dell'intero listino;
- storico prezzi senza prodotto/offerta => bloccato;
- ricerca senza filtro => bloccata salvo richiesta esplicita del catalogo completo;
- fallback al parser controllato R4.1 se Ollama non è disponibile;
- nessun pulsante di download dell'intero listino dopo una semplice richiesta di conteggio;
- suggerimenti di valori reali quando un'entità non viene trovata;
- diagnostica motore/modello nell'interfaccia.

## Esempio fail-safe

Richiesta:

`Scaricami il listino Pfizer`

Se Pfizer non è nel database:

- il modello estrae `supplier = Pfizer`;
- il resolver locale non trova corrispondenza;
- `Fornitore` viene marcato come non risolto;
- la query non viene eseguita;
- nessun file Excel viene prodotto.
