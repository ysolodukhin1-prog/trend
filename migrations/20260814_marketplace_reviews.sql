CREATE TABLE IF NOT EXISTS public.marketplace_reviews (
    review_key text PRIMARY KEY,
    source_review_id text NOT NULL,
    marketplace text NOT NULL CHECK (marketplace IN ('ozon', 'wb')),
    product_id text NOT NULL,
    seller_article text,
    product_name text,
    category_name text,
    brand text,
    review_date date,
    rating numeric(3, 2),
    review_text text,
    pros text,
    cons text,
    answer_text text,
    answer_available boolean NOT NULL DEFAULT false,
    answered boolean,
    has_photo boolean,
    likes integer,
    order_status text,
    source text NOT NULL DEFAULT 'seller_api',
    source_synced_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_marketplace_reviews_date
    ON public.marketplace_reviews (review_date DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_reviews_marketplace_date
    ON public.marketplace_reviews (marketplace, review_date DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_reviews_product
    ON public.marketplace_reviews (marketplace, product_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_reviews_rating
    ON public.marketplace_reviews (rating);
CREATE INDEX IF NOT EXISTS idx_marketplace_reviews_answered
    ON public.marketplace_reviews (answered)
    WHERE answer_available;

CREATE TABLE IF NOT EXISTS public.marketplace_review_sync_runs (
    run_id bigserial PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    source text NOT NULL DEFAULT 'seller_api',
    marketplaces text[] NOT NULL DEFAULT ARRAY[]::text[],
    products_total integer NOT NULL DEFAULT 0,
    products_processed integer NOT NULL DEFAULT 0,
    reviews_loaded integer NOT NULL DEFAULT 0,
    products_failed integer NOT NULL DEFAULT 0,
    error_summary text
);
