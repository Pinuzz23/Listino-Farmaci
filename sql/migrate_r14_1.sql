-- R14.1 · Hardening validità e dashboard PostgreSQL/Supabase.
-- Migrazione idempotente.

CREATE UNIQUE INDEX IF NOT EXISTS uq_product_validity_history_source
    ON public.product_validity_history(product_id, source_batch_id)
    WHERE source_batch_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.custom_dashboards (
    dashboard_id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.dashboard_widgets (
    widget_id BIGSERIAL PRIMARY KEY,
    dashboard_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    widget_type TEXT NOT NULL,
    config_json TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    width TEXT NOT NULL DEFAULT 'half',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT fk_widgets_dashboard
        FOREIGN KEY (dashboard_id)
        REFERENCES public.custom_dashboards(dashboard_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard
    ON public.dashboard_widgets(dashboard_id, position, widget_id);

-- Le dashboard sono usate dal server Streamlit tramite connessione PostgreSQL
-- diretta. RLS viene abilitata per impedire accesso implicito via Data API.
ALTER TABLE public.custom_dashboards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dashboard_widgets ENABLE ROW LEVEL SECURITY;
