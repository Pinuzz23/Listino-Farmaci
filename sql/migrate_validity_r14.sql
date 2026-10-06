-- R14 · Validità farmaco / monitoraggio shelf-life
-- Migrazione PostgreSQL idempotente.

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS data_validita_farmaco DATE;

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS validita_riferimento_at TIMESTAMPTZ;

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS validita_iniziale_giorni INTEGER;

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS validita_updated_at TIMESTAMPTZ;

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS validita_updated_by UUID;

CREATE TABLE IF NOT EXISTS public.product_validity_history (
    validity_history_id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    old_valid_until DATE,
    new_valid_until DATE NOT NULL,
    reference_at TIMESTAMPTZ NOT NULL,
    initial_days INTEGER NOT NULL,
    reason TEXT,
    source_batch_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID,
    CONSTRAINT fk_product_validity_history_product
        FOREIGN KEY (product_id)
        REFERENCES public.products(product_id),
    CONSTRAINT product_validity_history_event_check
        CHECK (event_type IN ('FIRST_LOAD', 'TRACE_RENEWAL', 'BUYER_RENEWAL'))
);

CREATE INDEX IF NOT EXISTS idx_product_validity_history_product
    ON public.product_validity_history(product_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_products_validity
    ON public.products(data_validita_farmaco);
