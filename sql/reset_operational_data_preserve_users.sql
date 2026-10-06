-- R14.1 · Reset dati operativi preservando utenze e sicurezza.
-- Target: PostgreSQL / Supabase.
--
-- PRESERVATI:
--   auth.*
--   public.user_profiles
--   public.roles
--   public.permissions
--   public.role_permissions
--   public.access_requests
--   public.audit_log
--
-- ATTENZIONE:
-- Gli oggetti fisici nel bucket Supabase Storage non vanno cancellati via SQL.
-- Rimuoverli tramite Storage API/dashboard, quindi eventualmente verificare
-- che storage.objects non contenga residui.

BEGIN;

TRUNCATE TABLE
    public.dashboard_widgets,
    public.custom_dashboards,
    public.erp_delta_events,
    public.erp_export_packages,
    public.product_validity_history,
    public.price_history,
    public.offers,
    public.publication_rows,
    public.products,
    public.publications,
    public.product_documents
RESTART IDENTITY;

COMMIT;

-- Verifica rapida post-reset:
-- SELECT
--   (SELECT COUNT(*) FROM public.products) AS products,
--   (SELECT COUNT(*) FROM public.offers) AS offers,
--   (SELECT COUNT(*) FROM public.publications) AS publications,
--   (SELECT COUNT(*) FROM auth.users) AS auth_users,
--   (SELECT COUNT(*) FROM public.user_profiles) AS user_profiles;
