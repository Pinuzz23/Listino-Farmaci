# Release R10 — Master definitivo 22 campi

## Tracciato

- baseline aggiornata da 18 a 22 campi;
- aggiunto `Forma Farmaceutica` obbligatorio;
- aggiunte dimensioni `X`, `Y`, `Z` obbligatorie, espresse in cm e maggiori di zero;
- template ufficiale aggiornato a `templates/Template_master_farma_def.xlsx`.

## Validazione

- il lettore continua a rilevare intestazioni in riga 1 o 2;
- `Forma Farmaceutica`, `X`, `Y`, `Z` sono bloccanti se mancanti;
- `X`, `Y`, `Z` accettano numeri decimali e sono bloccanti se <= 0;
- editor, dataset normalizzato e report validato ereditano automaticamente le 22 colonne dallo schema.

## Catalogo

- aggiunte a `products` le colonne `forma_farmaceutica`, `x`, `y`, `z` tramite compatibility layer R10;
- migrazione PostgreSQL/SQLite non distruttiva;
- pubblicazione, preview variazioni, catalogo ed export includono i nuovi campi;
- i record storici possono mantenere `NULL` nei quattro nuovi campi.

## Test

- aggiunto `scripts/test_master22.py` per struttura, template, validazione e persistenza;
- aggiornato il fixture del Validation Copilot R9 al nuovo master.
