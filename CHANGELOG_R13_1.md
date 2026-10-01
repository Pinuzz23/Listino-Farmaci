# R13.1 · ERP Delta Reconciliation

## Correzione

La pubblicazione Buyer può risultare correttamente presente nel listino ma, in caso di errore successivo al commit della pubblicazione, non avere tutti gli eventi nella coda ERP.

R13.1 introduce un controllo di consistenza idempotente:

- prende come fonte `publication_rows`;
- considera solo le azioni che devono produrre delta ERP (`INSERT` / `UPDATE`);
- usa lo stesso `event_key` R12 (`BUYER|batch_id|offer_key`);
- crea esclusivamente gli eventi mancanti con `ON CONFLICT DO NOTHING`;
- limita il recupero alle pubblicazioni dalla R12 in avanti, evitando il backfill del catalogo storico pre-R12;
- verifica la coda anche quando viene aperta la pagina Export ERP.

## Batch segnalato

Il batch `PUB-20261001-125012-C903` rientra nella finestra R12 e viene quindi incluso nel controllo automatico.

## Attivazione

Il guard viene caricato:

- all'avvio di `app.py`, dopo il layer performance R13;
- in `modules/catalog_db.py`, prima degli import operativi R12.

In questo modo sia le nuove pubblicazioni sia la pagina Export ERP usano il controllo di consistenza.
