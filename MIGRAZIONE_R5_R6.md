# Migrazione locale R5 → R6

Se vuoi mantenere il listino già pubblicato nella R5:

1. chiudi la R5;
2. fai una copia di sicurezza della cartella R5;
3. copia il file `data/listino.db` dalla R5 nella cartella `data` della R6;
4. facoltativamente copia anche `archive/` e `backup/`;
5. avvia la R6.

Al primo accesso al database la R6 esegue automaticamente la migrazione necessaria per rendere interrogabile anche `Fala / Lasa`.

Quando possibile, i valori storici di `Fala / Lasa` vengono ricostruiti dai JSON già presenti nella tabella di audit `publication_rows`.

Non cancellare il database R5 prima di aver verificato la nuova release.
