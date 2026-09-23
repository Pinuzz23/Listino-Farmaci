# R7 — Dashboard Builder

## Nuova area: Le mie Dashboard

La R7 aggiunge un builder self-service per creare dashboard personali direttamente sui dati del listino pubblicato.

### Widget disponibili

- KPI
- Grafico a barre
- Top N
- Tabella dinamica
- Trend prezzi basato sullo storico pubblicazioni

### Funzioni

- creazione e cancellazione dashboard;
- modifica nome e descrizione;
- aggiunta/modifica/eliminazione widget;
- riordino widget con frecce su/giù;
- layout a mezza riga o riga intera;
- filtro specifico per singolo widget;
- filtri globali applicati simultaneamente a tutta la dashboard;
- filtro per range di Prezzo Unitario o Prezzo Confezione;
- dashboard salvate nel database SQLite e aggiornate automaticamente sui dati correnti;
- modelli pronti: Overview Listino, Analisi Prezzi, Logistica.

### Persistenza

Sono state aggiunte al database SQLite le tabelle:

- `custom_dashboards`
- `dashboard_widgets`

La configurazione delle dashboard viene memorizzata come JSON controllato. Nessun dato di listino viene duplicato nelle dashboard.

### Sicurezza / separazione dati

Il Dashboard Builder legge esclusivamente il listino pubblicato. Non accede ai tracciati ancora in validazione e non esegue SQL generato dall'utente.

### Compatibilità

La R7 può utilizzare direttamente un `data/listino.db` proveniente dalla R6/R6.1. Le nuove tabelle dashboard vengono create automaticamente al primo avvio.
