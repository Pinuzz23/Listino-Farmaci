# Listino Farmaci — Wave 2 Demo R9

Applicazione locale Streamlit per validazione e correzione assistita dei tracciati farmaceutici, riconciliazione AIFA, pubblicazione su listino SQLite, Assistente Listino AI e dashboard self-service.

## Novità R9: Validation Copilot con WRITE controllati

La R9 introduce un vero flusso operativo AI:

```text
richiesta utente
    ↓
LLM / planner locale
    ↓
Tool Registry 2.0
    ↓
Change Set proposto
    ↓
anteprima + selezione
    ↓
simulazione prima/dopo
    ↓
conferma operatore
    ↓
WRITE controllato
    ↓
rivalidazione completa
```

L'LLM **non modifica mai direttamente il dataset** e non genera SQL. Può invocare tool di lettura e di proposta; l'applicazione effettiva è eseguita da Python solo dopo conferma nell'interfaccia.

## Avvio rapido

Da Visual Studio Code:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

In alternativa su Windows è disponibile `AVVIA_DEMO.bat`.

## Sezioni

- **Dashboard** — panoramica operativa.
- **Validazione** — upload, Validation Copilot R9, AIFA, correzione manuale e pubblicazione.
- **Listino prodotti** — ricerca, filtri, dettaglio e storico prezzi.
- **Assistente Listino** — interrogazione conversazionale via Ollama / fallback controllato.
- **Le mie Dashboard** — builder self-service di KPI, grafici, Top N, tabelle e trend prezzi.
- **Pubblicazioni** — audit dei batch pubblicati.

## Copilot Validazione R9

Esempi:

```text
Perché questo file non passa?
Da dove comincio a correggere?
Correggi automaticamente tutto quello che puoi.
Sistema gli errori di prezzo confezione.
Correggi i problemi di stivaggio.
Allinea i principi attivi con AIFA.
Cosa non va alla riga 17?
Spiegami il campo Minimo Movimentabile.
```

### Change Set

Ogni proposta mostra:

- riga Excel;
- campo;
- valore precedente;
- valore proposto;
- fonte;
- confidence;
- rischio;
- motivazione.

Le modifiche `sensitive` sono deselezionate per default e richiedono una conferma aggiuntiva.

### Simulazione

Il pulsante `Simula impatto` applica le sole modifiche selezionate a una copia temporanea e mostra il numero di errori/warning attesi dopo la correzione. Il dataset reale non viene toccato.

### Applicazione e Undo

`Applica selezionate` esegue il tool WRITE dopo conferma e rivalida completamente il file. La sessione conserva fino a 10 checkpoint e consente `Annulla ultima modifica AI`.

## Tool Registry 2.0

### READ

- `validation_summary`
- `validation_list_issues`
- `validation_group_issues`
- `validation_show_row`
- `validation_explain_issue`
- `validation_correction_plan`
- `validation_aifa_findings`
- `validation_normalizations`
- `validation_operator_changes`
- `validation_describe_field`
- `validation_business_rules`

### GENERATE

- `validation_propose_package_price_fix`
- `validation_propose_storage_group_fix`
- `validation_propose_aifa_alignment`
- `validation_propose_supplier_from_aifa`
- `validation_propose_identical_duplicate_deletion`
- `validation_propose_all_deterministic`

### WRITE

- `validation_apply_change_set`

I tool WRITE non vengono mai invocati dal planner LLM.

## Regole di correzione disponibili

### Prezzo Confezione

Propone esclusivamente:

```text
Prezzo Confezione = Prezzo Unitario × UPC
```

Non modifica autonomamente Prezzo Unitario o UPC.

### Stivaggio

Utilizza le regole configurate nel master. S7/S8 hanno priorità; le temperature vengono trasformate in gruppo solo nelle casistiche deterministiche definite.

### AIFA

Con match AIC esatto può proporre l'allineamento di:

- Nome Commerciale;
- Principio Attivo;
- ATC7.

La sovrascrittura di un valore già presente è `sensitive`. Azienda titolare AIFA → Fornitore è sempre una proposta sensibile.

### Duplicati

Sono proponibili per eliminazione solo duplicati completamente identici sui 18 campi. Il sistema non decide automaticamente quale riga mantenere se i dati differiscono.

## Knowledge layer

La semantica del dominio è centralizzata in:

```text
config/data_dictionary.json
config/business_rules.json
```

Questo aiuta l'LLM a spiegare correttamente concetti come UPC, Minimo Movimentabile, Azienda AIFA vs Fornitore e regole prezzo/stivaggio.

## Audit AI

Le interazioni e le azioni controllate vengono registrate in:

```text
logs/validation_ai_audit.jsonl
```

L'audit non blocca il flusso operativo in caso di problemi di scrittura del log.

## Database

Il listino locale rimane in:

```text
data/listino.db
```

La R9 non introduce una migrazione obbligatoria del database rispetto alla R7/R8. Per mantenere i dati già pubblicati puoi copiare il tuo `listino.db` nella cartella `data` della R9.

## Test

```powershell
python scripts\test_validation_copilot_r9.py
python scripts\test_validation_copilot_r8.py
python scripts\test_assistant_r6.py
python scripts\test_dashboard_r7.py
```

## Ollama

Per l'installazione e i modelli locali consulta `OLLAMA_SETUP.md`. Se Ollama non è disponibile, il Copilot mantiene un planner deterministico di fallback per le richieste principali.
