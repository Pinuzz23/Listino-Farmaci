-- R12 - Order Management & Delta ERP
-- Migrazione non distruttiva. Il bootstrap applicativo esegue la stessa logica automaticamente.

BEGIN;

INSERT INTO public.roles (role_id, display_name, description)
VALUES (
    'ORDER_MANAGEMENT',
    'Order Management',
    'Gestione operativa del listino e dei delta destinati al gestionale ERP.'
)
ON CONFLICT (role_id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description;

INSERT INTO public.permissions (permission_id, display_name, description)
VALUES
    (
        'edit_catalogue',
        'Modifica catalogo',
        'Consente di correggere i dati correnti di prodotto e offerta con audit.'
    ),
    (
        'export_erp',
        'Export ERP',
        'Consente di preparare, scaricare e confermare i delta CSV verso ERP.'
    )
ON CONFLICT (permission_id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT 'ORDER_MANAGEMENT', permission_id
FROM public.permissions
WHERE permission_id IN (
    'access_app',
    'view_catalogue',
    'export_catalogue',
    'view_publications',
    'view_history',
    'manage_catalogue',
    'edit_catalogue',
    'export_erp'
)
ON CONFLICT DO NOTHING;

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT 'ADMIN', permission_id
FROM public.permissions
WHERE permission_id IN ('manage_catalogue', 'edit_catalogue', 'export_erp')
ON CONFLICT DO NOTHING;

DELETE FROM public.role_permissions
WHERE permission_id IN ('edit_catalogue', 'export_erp')
  AND role_id NOT IN ('ADMIN', 'ORDER_MANAGEMENT');

CREATE TABLE IF NOT EXISTS public.erp_export_packages (
    package_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'EXPORTED',
    confirmed_at TIMESTAMPTZ,
    confirmed_by UUID,
    CONSTRAINT erp_export_packages_status_check
        CHECK (status IN ('EXPORTED', 'IMPORTED'))
);

CREATE TABLE IF NOT EXISTS public.erp_delta_events (
    event_id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    product_id BIGINT,
    offer_id BIGINT,
    action TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_batch_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID,
    export_status TEXT NOT NULL DEFAULT 'PENDING',
    export_package_id TEXT,
    exported_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ,
    imported_by UUID,
    CONSTRAINT erp_delta_action_check
        CHECK (action IN ('INSERT', 'UPDATE', 'DISABLE', 'REACTIVATE')),
    CONSTRAINT erp_delta_status_check
        CHECK (export_status IN ('PENDING', 'EXPORTED', 'IMPORTED')),
    CONSTRAINT fk_erp_delta_package
        FOREIGN KEY (export_package_id)
        REFERENCES public.erp_export_packages(package_id)
);

CREATE INDEX IF NOT EXISTS idx_erp_delta_status
    ON public.erp_delta_events(export_status, created_at);
CREATE INDEX IF NOT EXISTS idx_erp_delta_batch
    ON public.erp_delta_events(source_batch_id);

COMMIT;
