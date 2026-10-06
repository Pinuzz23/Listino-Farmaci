# Listino Farmaci — R14.1

Applicazione Streamlit per validazione dei tracciati farmaceutici, riconciliazione AIFA, pubblicazione del listino su PostgreSQL/Supabase con fallback SQLite locale, gestione ERP, dashboard e monitoraggio della validità commerciale.

## R14.1

La R14.1 consolida la R14 sul master definitivo a **23 campi**.

Principali funzioni:

- validazione Excel con controlli strutturali, AIFA, prezzi e stivaggio;
- `Data Validità Farmaco` con stati **REGOLARE / ATTENZIONE / CRITICO / SCADUTO / SENZA DATA**;
- rinnovo validità Buyer/Admin con motivazione, storico e delta ERP;
- guard idempotente che ricostruisce la persistenza validità se il core della pubblicazione è già stato confermato;
- coda delta ERP Buyer e Order Management;
- Assistente Listino locale via Ollama con fallback controllato;
- query su stato validità, giorni residui e scadenze entro N giorni;
- dashboard self-service con backend PostgreSQL/Supabase o SQLite;
- modello dashboard Validità pronto all'uso;
- template R14 generato automaticamente se non è presente nel deployment;
- CI GitHub Actions con test unitari e di integrazione.

## Master 23 campi

1. Fornitore
2. AIC
3. Codice Fornitore
4. Nome Commerciale
5. Principio Attivo
6. Forma Farmaceutica
7. Materiale Pericoloso
8. Stupefacente
9. ATC7
10. ATC9
11. Fala / Lasa
12. Gruppo di Stivaggio
13. Temperatura di Stivaggio
14. Prezzo Unitario
15. Prezzo Confezione
16. UPC
17. Minimo Movimentabile
18. IVA
19. Note
20. X
21. Y
22. Z
23. Data Validità Farmaco

Il template usa riga 1 per le indicazioni, riga 2 per le intestazioni e i dati dalla riga 3. Sono accettati anche file operativi con intestazioni direttamente in riga 1.

## Validità farmaco

La finestra parte quando una specifica validità entra a listino per la prima volta.

- stessa data ripubblicata: la finestra **non viene resettata** e non viene creato un nuovo evento storico;
- dal 50% della vita iniziale: **ATTENZIONE**;
- da 2/3: **CRITICO**;
- alla data limite: **SCADUTO**;
- una data successiva crea un rinnovo;
- un rinnovo con data uguale o precedente viene rifiutato.

Il rinnovo manuale è disponibile a Buyer/Admin e genera storico e delta ERP `UPDATE`.

### Recovery R14.1

`modules/validity_reconciliation_guard.py` usa `publication_rows` come sorgente immutabile. Se la pubblicazione core è stata salvata ma la persistenza della validità si interrompe, il guard ricostruisce solo i dati mancanti in modo idempotente senza riportare indietro rinnovi successivi.

## Assistente Listino

Esempi:

```text
Mostrami i farmaci critici
Quali farmaci scadono entro 60 giorni?
Quanti farmaci sono in attenzione?
Scaricami il listino del fornitore Angelini
Mostrami i prodotti in frigo con IVA 10%
Storico prezzi dell'AIC 012745055
```

L'LLM interpreta la frase dell'utente. Grounding, filtri e accesso ai dati restano controllati dal codice Python; l'LLM non esegue SQL.

## Dashboard

Le dashboard personalizzate salvano soltanto configurazione e widget. I dati sono letti sempre dal listino corrente.

In produzione la configurazione viene salvata nelle tabelle PostgreSQL/Supabase:

```text
public.custom_dashboards
public.dashboard_widgets
```

In locale viene usato lo stesso modello su SQLite.

## Database

Il backend è PostgreSQL/Supabase quando è configurato `DATABASE_URL` o Streamlit `[database].url`. In assenza di configurazione viene usato SQLite locale.

Le strutture utenti sono separate dai dati operativi. Lo script:

```text
sql/reset_operational_data_preserve_users.sql
```

azzera il dominio operativo senza eliminare Auth, profili, ruoli, permessi, richieste di accesso o audit utenti.

## Avvio locale

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Test

```powershell
python scripts\test_validity_r14.py
python scripts\test_validity_r14_integration.py
python scripts\test_template_r14.py
python scripts\test_assistant_r14_1.py
python scripts\test_dashboard_r7.py
```

La CI esegue inoltre `compileall` a ogni push/PR sulle branch configurate.

## Knowledge layer

Le definizioni e le regole di dominio usate dal Validation Copilot sono centralizzate in:

```text
config/data_dictionary.json
config/business_rules.json
```

## Ollama

Per configurazione e modelli locali consulta `OLLAMA_SETUP.md`.
