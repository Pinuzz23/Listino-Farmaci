BEGIN;

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS forma_farmaceutica TEXT,
    ADD COLUMN IF NOT EXISTS x DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS y DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS z DOUBLE PRECISION;

COMMIT;

SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'products'
  AND column_name IN ('forma_farmaceutica', 'x', 'y', 'z')
ORDER BY column_name;
