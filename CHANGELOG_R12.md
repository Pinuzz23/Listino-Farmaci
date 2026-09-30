# R12 - Order Management & Delta ERP

## Obiettivo

Separare il flusso operativo tra Buyer e Order Management:

1. il Buyer valida e pubblica i tracciati fornitore;
2. ogni riga nuova o modificata genera un delta ERP incrementale;
3. Order Management può correggere il catalogo corrente, gestire visibilità/archiviazione e produrre CSV per l'importatore ERP;
4. i pacchetti restano tracciati fino alla conferma di avvenuta importazione.

## Nuovo ruolo

`ORDER_MANAGEMENT` con permessi:

- accesso app;
- visualizzazione listino;
- export listino;
- visualizzazione pubblicazioni e storico;
- gestione visibilità/archiviazione catalogo;
- modifica controllata prodotto/offerta;
- export e conferma delta ERP.

Il ruolo non riceve i permessi di validazione/pubblicazione dei tracciati Buyer né di gestione utenti.

## Delta ERP

Azioni supportate:

- `INSERT`: nuovo prodotto o nuova offerta pubblicata dal Buyer;
- `UPDATE`: variazione Buyer o correzione Order Management;
- `DISABLE`: archiviazione/eliminazione logica dal listino;
- `REACTIVATE`: ripristino di un articolo archiviato.

Stati del trasferimento:

- `PENDING`: ancora da esportare;
- `EXPORTED`: inserito in un pacchetto CSV;
- `IMPORTED`: importazione ERP confermata dall'operatore.

## Export

Nuova pagina **Export ERP** con:

- filtri per origine, azione, batch, AIC, prodotto e fornitore;
- creazione di un CSV cumulativo sui delta filtrati;
- possibilità di riscaricare un pacchetto;
- conferma manuale dell'importazione ERP;
- export CSV del listino corrente o di una sua porzione filtrata.

Il mapping CSV è configurabile in `config/erp_export.json` e potrà essere adattato al tracciato tecnico definitivo dell'importatore ERP.

## Modifica catalogo

La pagina Gestione catalogo consente a Admin e Order Management di correggere i campi non identificativi del prodotto e dell'offerta. `AIC`, `Fornitore` e `Codice Fornitore` restano protetti. Ogni modifica richiede una motivazione, viene auditata e genera un evento `UPDATE` per ERP.

## Database

Nuove tabelle non distruttive:

- `erp_delta_events`
- `erp_export_packages`

La migrazione viene applicata automaticamente al bootstrap; lo script manuale di fallback è `sql/migrate_order_management_r12.sql`.
