# R11 - Gestione visibilità catalogo

## Obiettivo

Consentire esclusivamente agli utenti ADMIN di controllare quali prodotti sono visibili nell'interfaccia Cliente, senza cancellare dati storici.

## Stati prodotto

- `VISIBLE`: prodotto disponibile nel listino Cliente e interno.
- `HIDDEN`: prodotto escluso dal listino Cliente ma ancora visibile agli utenti interni.
- `ARCHIVED`: prodotto rimosso dal listino operativo; resta disponibile nella Gestione catalogo Admin e conserva storico e documentazione.

## Funzioni introdotte

- Nuova pagina Admin **Gestione catalogo** con ricerca e filtro per stato.
- Azioni: nascondi, rendi visibile, archivia/elimina dal listino, ripristina visibile o nascosto.
- Motivazione obbligatoria per ogni cambio stato.
- Audit `CATALOGUE_STATUS_CHANGE` con stato precedente, nuovo stato e motivazione.
- Nuovo permesso RBAC `manage_catalogue`, assegnato automaticamente solo al ruolo `ADMIN`.
- Filtro backend role-aware: il profilo `CLIENTE` riceve solo prodotti `VISIBLE` anche in export.
- Le pubblicazioni successive non modificano automaticamente lo stato deciso dall'Admin.

## Database

Migrazione automatica e non distruttiva della tabella `products` con:

- `catalogue_status`
- `status_reason`
- `status_updated_at`
- `status_updated_by`

I prodotti esistenti vengono mantenuti come `VISIBLE`.
