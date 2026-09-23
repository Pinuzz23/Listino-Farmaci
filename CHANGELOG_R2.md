# Release R2 — allineamento Template TracciatoTest_2

## Modifiche integrate

- tracciato operativo ridotto a 17 colonne;
- rimozione di `Principio Attivo` dall'input e dall'export del tracciato validato;
- mantenimento di `Principio Attivo` come dato derivato AIFA nel listino elettronico;
- `Stupefacente` reso obbligatorio;
- aggiunto `No` all'elenco Stupefacente;
- rinominato `Minimo Movimentabile (n. confezioni)` in `Minimo Movimentabile`;
- allineate le 31 temperature al foglio `Elenchi` del nuovo template;
- mantenute le quattro classi di Gruppo di Stivaggio;
- mantenute IVA 0%, 4%, 10%, 22%;
- lettore Excel aggiornato per individuare le intestazioni anche quando iniziano in colonna B;
- la colonna tecnica `Campo` del template non viene più interpretata come colonna extra;
- i file operativi con intestazioni direttamente in colonna A continuano a essere supportati;
- regole S7/S8 → `STUPEF_Stupefacenti` confermate;
- regole temperatura → STD / FRIGO / FREEZER confermate;
- pubblicazione Wave 2 aggiornata alla nuova intestazione `Minimo Movimentabile`.

## Scelta sulla colonna AIC

Nel template `AIC` è indicato come obbligatorio, ma la linea guida di `Codice Fornitore` specifica esplicitamente il caso di assenza AIC. La release mantiene quindi la regola già concordata: **deve essere presente almeno uno tra AIC e Codice Fornitore**. Questo evita di bloccare articoli legittimamente privi di AIC.
