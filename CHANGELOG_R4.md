# Wave 2 Demo R4 — Assistente Listino

## Nuova funzione

Aggiunta la pagina **Assistente Listino**, completamente locale e senza servizi AI esterni.

L'assistente interpreta richieste in italiano e le converte in un set chiuso di filtri sul listino pubblicato. Non genera e non esegue SQL libero.

### Richieste supportate

- esportazione del listino per fornitore;
- ricerca/esportazione per Principio Attivo;
- ricerca per AIC;
- ricerca per ATC7/ATC9;
- filtri per Gruppo di Stivaggio (frigo/freezer/standard/stupefacenti);
- classificazioni S7/S8 e valori Stupefacente presenti nel listino;
- IVA 0/4/10/22%;
- soglie e range di Prezzo Confezione o Prezzo Unitario;
- conteggi;
- storico prezzi per AIC/offerta;
- generazione Excel dei risultati.

### Sicurezza applicativa

Il parser non ha accesso a comandi SQL. Opera sul DataFrame restituito dal repository del listino e applica esclusivamente campi/operazioni predefiniti.

Se l'utente cita esplicitamente un campo (es. Fornitore o Principio Attivo) ma il valore non viene riconosciuto con sufficiente certezza, la query viene bloccata invece di allargarsi all'intero listino.
