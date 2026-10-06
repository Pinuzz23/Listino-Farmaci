# R14 · Validità farmaco e rinnovo Buyer

## Obiettivo
Estendere il master definitivo da 22 a 23 campi aggiungendo **Data Validità Farmaco** come ultima colonna e introdurre il monitoraggio della vita residua commerciale dell'AIC.

## Regola di validità
- La finestra parte quando una specifica Data Validità Farmaco entra per la prima volta a listino.
- La vita iniziale è il numero di giorni tra data di riferimento e Data Validità Farmaco.
- Una ripubblicazione con la stessa data non azzera il conteggio.
- Da 50% a meno di 2/3 della vita consumata: **ATTENZIONE**.
- Da 2/3 in poi: **CRITICO**.
- Alla scadenza: **SCADUTO**.
- Se un tracciato ripresenta la stessa validità quando il prodotto è CRITICO, la validazione genera un errore bloccante.
- Una data successiva rappresenta un rinnovo e apre una nuova finestra; una data inferiore o uguale a quella corrente non è accettata come rinnovo.

## Master 23 campi
Il nuovo campo obbligatorio è l'ultimo:
1-22. campi R10 esistenti
23. **Data Validità Farmaco**

Formato consigliato: `GG/MM/AAAA`. Il valore deve essere una data futura.

## Monitor validità
Nuova pagina **Monitor validità** per Buyer, Admin e Order Management con:
- KPI Regolari / In attenzione / Critici / Scaduti / Senza data;
- ricerca per AIC, prodotto e fornitore;
- filtri per stato e giorni residui;
- giorni residui, percentuale di vita consumata e data soglia 2/3;
- storico delle finestre di validità.

## Rinnovo Buyer
Buyer e Admin possono eseguire **Rinnovo validità**:
- nuova data obbligatoriamente successiva alla validità corrente;
- motivazione obbligatoria;
- nuova data di riferimento = momento del rinnovo;
- percentuale di vita consumata riparte da 0%;
- storico persistente e audit `VALIDITY_RENEWAL`;
- generazione automatica di un delta ERP `UPDATE`.

## ERP
`Data Validità Farmaco` è inclusa come ultima colonna:
- nel delta Buyer;
- nei rinnovi Buyer;
- nell'export CSV del listino corrente.

## Compatibilità
I prodotti pubblicati prima della R14 non vengono inventati o retrodatati: finché non ricevono una validità tramite un nuovo tracciato R14 o un rinnovo esplicito, il monitor li mostra come **SENZA DATA**.
