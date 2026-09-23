# Wave 2 Demo R6 — Assistente AI esteso a tutto il tracciato

Data: 21/09/2026

## Novità principali

- L'Assistente LLM può interrogare tutti i 18 campi del tracciato master.
- Aggiunti confronti numerici su Prezzo Unitario, Prezzo Confezione, UPC, Minimo Movimentabile e IVA.
- Aggiunti ordinamento e ranking: massimo, minimo, top N, bottom N.
- Aggiunte aggregazioni: massimo, minimo, media, somma e conteggio valori distinti.
- Supportati filtri testuali su Note e Codice Fornitore.
- Aggiunti Fala / Lasa e Note al dataset interrogabile del listino.
- Il database viene migrato automaticamente: la colonna Fala / Lasa viene creata e, quando possibile, ricostruita dallo storico `publication_rows` delle pubblicazioni precedenti.
- Mantenuti i fail-safe R5: un valore non riconosciuto non può mai trasformarsi in un'estrazione dell'intero listino.
- Il modello continua a non generare SQL e non riceve le righe del database.

## Esempi ora gestibili

```text
Estrai il prodotto con il prezzo unitario più alto
Mostrami i 10 prodotti con prezzo confezione più basso
Prodotti con UPC maggiore di 100
Prodotti con minimo movimentabile tra 2 e 10
Prodotti con Materiale Pericoloso = Y
Prodotti Fala / Lasa = Y
Scarica i prodotti con temperatura da + 2 a 8°C
Note contengono urgente
Qual è il prezzo confezione medio?
Qual è il prezzo unitario massimo di Angelini?
Quanti principi attivi distinti ci sono?
```

## Sicurezza

Una richiesta di export senza filtri, ranking o esplicita richiesta dell'intero catalogo viene bloccata. Ad esempio, un fornitore inesistente continua a produrre zero estrazioni e nessun file Excel.
