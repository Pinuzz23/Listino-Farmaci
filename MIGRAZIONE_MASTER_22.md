# Migrazione Master definitivo a 22 campi

La baseline del tracciato passa da 18 a 22 campi. I nuovi campi obbligatori sono:

- `Forma Farmaceutica` (testo)
- `X` (lunghezza confezione in cm, numero > 0)
- `Y` (larghezza confezione in cm, numero > 0)
- `Z` (altezza confezione in cm, numero > 0)

## Database

La migrazione è non distruttiva. `products` riceve le colonne `forma_farmaceutica`, `x`, `y`, `z`; i record storici restano validi con valori `NULL`. Il compatibility layer R10 esegue anche gli `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` all'inizializzazione.

Per applicare manualmente la migrazione Supabase è disponibile `sql/migrate_master_22_columns.sql`.

## Template

Il template di riferimento è `templates/Template_master_farma_def.xlsx`, con linee guida in riga 1 e intestazioni ufficiali in riga 2. Su un deploy pulito il file viene rigenerato dallo schema se non è ancora presente.

## Compatibilità

I nuovi caricamenti devono rispettare il tracciato a 22 colonne. Le pubblicazioni già presenti non vengono eliminate né riscritte.
