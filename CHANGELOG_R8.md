# Changelog R8 — Validation Copilot + Tool Registry

## Nuovo Tool Registry

Introdotto `modules/tool_registry.py`, registro controllato di funzioni invocabili dall'AI.

Ogni tool dichiara:
- nome;
- descrizione;
- parametri;
- livello di accesso (`read`, `generate`, `write`);
- funzione Python autorizzata.

La R8 abilita nel Copilot di validazione solo tool `read`.

## Tool di validazione disponibili

- `validation_summary`
- `validation_list_issues`
- `validation_group_issues`
- `validation_show_row`
- `validation_explain_issue`
- `validation_correction_plan`
- `validation_aifa_findings`
- `validation_normalizations`
- `validation_operator_changes`

## Validation Copilot

Aggiunta nella pagina Validazione la scheda `🤖 Copilot Validazione`.

Esempi di richieste:
- `Perché questo file non passa la validazione?`
- `Da dove comincio a correggere?`
- `Mostrami gli errori bloccanti e le righe coinvolte.`
- `Qual è il problema più frequente?`
- `Spiegami COERENZA_PREZZO_CONFEZIONE.`
- `Cosa ha trovato AIFA?`
- `Cosa non va alla riga 17?`

## Architettura AI

Flusso:

`richiesta -> planner LLM/fallback -> tool registry -> risultati deterministici -> risposta LLM/fallback`

Il modello non accede a SQL e non modifica il tracciato.

## Guardrail

- massimo 4 tool per richiesta;
- solo tool registrati;
- solo accesso `read` nel Copilot R8;
- nessuna modifica automatica di celle;
- fallback deterministico se Ollama non è disponibile;
- audit locale delle richieste in `logs/validation_ai_audit.jsonl`.
