# R6.1 - Ranking prezzi

Correzione del grounding LLM per richieste di ranking come:

- `estrai il prodotto con prezzo unitario più basso`
- `estrai il prodotto con il prezzo unitario più alto`
- `mostrami i 10 prodotti con prezzo confezione più alto`

## Fix

Alcuni modelli Ollama potevano restituire il campo usato per il ranking anche come filtro vuoto (`value: null`). Il guardrail lo interpretava come filtro incompleto e bloccava la query.

La R6.1:

- inferisce il ranking direttamente dal testo utente;
- ignora esclusivamente i filtri numerici vuoti che duplicano il campo di ranking;
- mantiene il blocco per filtri numerici realmente incompleti;
- rafforza il prompt LLM affinché per ranking usi `sort + limit` e non filtri senza soglia;
- aggiunge un test di regressione specifico.
