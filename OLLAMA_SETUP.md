# Setup Assistente LLM locale (Ollama)

La R6 può usare un modello linguistico eseguito interamente sul PC tramite **Ollama**.
I dati del listino non vengono inviati al modello: il modello riceve solo la frase dell'utente e restituisce un'intenzione strutturata. Il programma verifica poi i valori contro SQLite prima di eseguire qualsiasi ricerca o export.

## 1. Installare Ollama

Installare Ollama sul PC Windows che ospita l'applicazione. In ambiente aziendale potrebbe essere necessaria l'autorizzazione IT.

Dopo l'installazione, verificare da PowerShell:

```powershell
ollama --version
```

## 2. Installare almeno un modello locale

Scegliere un modello instruct compatibile con Ollama e scaricarlo con:

```powershell
ollama pull <nome-modello>
```

Per vedere i modelli installati:

```powershell
ollama list
```

Non è necessario scrivere il nome del modello nella configurazione: l'app legge automaticamente i modelli installati e li mostra nella pagina **Assistente Listino AI**.

## 3. Avviare l'app

```powershell
python -m streamlit run app.py
```

Ollama usa normalmente l'endpoint locale:

```text
http://127.0.0.1:11434
```

che è già configurato in `config/schema.json`.

## 4. Fail-safe

Esempio: se il database contiene solo ANGELINI e JANSSEN e l'utente scrive:

```text
Scaricami il listino Pfizer
```

il modello estrae `supplier = Pfizer`, ma il programma non trova Pfizer tra i fornitori pubblicati. L'operazione viene quindi bloccata e nessun Excel viene generato.

Un export senza filtri è ammesso soltanto con una richiesta esplicita come:

```text
Scarica tutto il listino
```

Se Ollama non è raggiungibile, l'app può usare il parser controllato della R4.1 come fallback, che applica le stesse protezioni sull'export.
