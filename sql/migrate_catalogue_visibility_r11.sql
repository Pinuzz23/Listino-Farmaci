-- R11 - Gestione visibilità catalogo
-- Migrazione non distruttiva. I prodotti esistenti restano VISIBLE.

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS catalogue_status TEXT NOT NULL DEFAULT 'VISIBLE';
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS status_reason TEXT;
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ;
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS status_updated_by UUID;

UPDATE public.products
SET catalogue_status = 'VISIBLE'
WHERE catalogue_status IS NULL
   OR TRIM(catalogue_status) = '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
          AND t.relname = 'products'
          AND c.conname = 'products_catalogue_status_check'
    ) THEN
        ALTER TABLE public.products
        ADD CONSTRAINT products_catalogue_status_check
        CHECK (catalogue_status IN ('VISIBLE', 'HIDDEN', 'ARCHIVED'));
    END IF;
END $$;

INSERT INTO public.permissions (permission_id, display_name, description)
VALUES (
    'manage_catalogue',
    'Gestione catalogo',
    'Consente di nascondere, archiviare e ripristinare prodotti nel catalogo.'
)
ON CONFLICT (permission_id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT 'ADMIN', 'manage_catalogue'
WHERE NOT EXISTS (
    SELECT 1
    FROM public.role_permissions
    WHERE role_id = 'ADMIN'
      AND permission_id = 'manage_catalogue'
);

DELETE FROM public.role_permissions
WHERE permission_id = 'manage_catalogue'
  AND role_id <> 'ADMIN';
