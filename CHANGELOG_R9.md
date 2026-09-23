# Changelog R9 — AI Validation Copilot con WRITE controllati

La R9 evolve il Copilot R8 da assistente di sola lettura a sistema di **proposta, simulazione e applicazione controllata delle correzioni**.

## Tool Registry 2.0

Ogni tool dichiara ora:

- `access`: `read`, `generate`, `write`;
- `risk`: `safe`, `controlled`, `sensitive`;
- `requires_confirmation`;
- `category`.

Il planner LLM può invocare solo tool `read` e `generate`. I tool `write` non sono esposti all'esecuzione autonoma del modello e richiedono una conferma esplicita dell'interfaccia.

## Change Set Engine

Le correzioni AI non modificano direttamente le celle. Viene creato un Change Set con:

- riga e campo;
- valore prima / dopo;
- fonte;
- motivazione;
- confidence;
- livello di rischio;
- tool proponente.

L'operatore può selezionare le singole modifiche, simulare l'impatto, confermare e applicare.

## Tool GENERATE disponibili

- `validation_propose_package_price_fix`
- `validation_propose_storage_group_fix`
- `validation_propose_aifa_alignment`
- `validation_propose_supplier_from_aifa`
- `validation_propose_identical_duplicate_deletion`
- `validation_propose_all_deterministic`

## Tool WRITE

- `validation_apply_change_set`

È invocato dall'applicazione solo dopo approvazione esplicita dell'operatore.

## Simulazione

Prima dell'applicazione è possibile rivalidare una copia temporanea e confrontare:

- errori bloccanti prima/dopo;
- warning prima/dopo;
- esito finale;
- numero di modifiche simulate.

## Undo

La R9 conserva fino a 10 checkpoint della sessione per permettere `Annulla ultima modifica AI`.

## AIFA

L'allineamento AIFA propone Nome Commerciale, Principio Attivo e ATC7 tramite match AIC esatto. Le sovrascritture di dati già valorizzati sono marcate come `sensitive`. La proposta di Azienda titolare AIFA come Fornitore è sempre `sensitive`.

## Correzioni deterministiche

`Correggi ciò che puoi` può proporre in un unico Change Set:

- Prezzo Confezione calcolato da Prezzo Unitario × UPC;
- Gruppo di Stivaggio derivato dalle regole configurate;
- completamenti/allineamenti AIFA non sensibili.

Le modifiche sensibili sono escluse per default.

## Duplicati

L'eliminazione viene proposta solo per duplicati perfettamente identici sui 18 campi. È sempre una modifica `sensitive`. Duplicati con valori discordanti non vengono risolti automaticamente.

## Knowledge layer

Nuovi file:

- `config/data_dictionary.json`
- `config/business_rules.json`

Il Copilot può spiegare semantica dei campi e regole senza affidarsi esclusivamente al prompt.

## Conversation context

Con Ollama il planner riceve gli ultimi turni della conversazione, migliorando le richieste di follow-up. L'applicazione di modifiche rimane comunque separata dalla chat.

## Audit

`logs/validation_ai_audit.jsonl` registra pianificazione, tool invocati, Change Set applicati/scartati e undo.

## Test

```powershell
python scripts\test_validation_copilot_r9.py
```

Il test verifica proposta, simulazione, blocco dei WRITE senza conferma e applicazione controllata.
