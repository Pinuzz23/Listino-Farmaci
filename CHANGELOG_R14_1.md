# CHANGELOG R14.1

## Hardening pubblicazione e validità

- introdotto `ValidityPersistenceError` per distinguere gli errori avvenuti dopo il commit del core;
- aggiunto `validity_reconciliation_guard` idempotente basato su `publication_rows`;
- aggiunto indice univoco per impedire doppi eventi validità sullo stesso batch/riferimento;
- la ripubblicazione della stessa validità non crea una nuova finestra e non genera un nuovo evento storico;
- recovery automatico su catalogo e monitor validità;
- test di integrazione del ciclo prima pubblicazione → stessa validità → rinnovo tracciato → recovery → rinnovo Buyer.

## Template e knowledge layer

- generazione automatica del template R14 se il file non è presente;
- dizionario dati aggiornato con `Data Validità Farmaco`;
- business rules aggiornate con lifecycle e rinnovo;
- versione schema aggiornata a `1.14.1-r14.1`.

## Assistente e dashboard

- campi R14 aggiunti all'Assistente Listino;
- supporto a `Stato Validità`, `Giorni Residui` e `Vita Consumata %`;
- fallback deterministico per richieste su farmaci critici/scaduti e scadenze entro N giorni;
- metriche validità disponibili nel dashboard builder;
- nuovo modello dashboard `Validità Farmaci`;
- persistenza dashboard resa compatibile con PostgreSQL/Supabase e SQLite.

## Database e qualità

- script ripetibile di reset operativo che preserva utenze, ruoli, permessi e audit;
- migrazione R14.1 idempotente;
- GitHub Actions CI con compilazione, test validità, integrazione, template, assistente e dashboard.
