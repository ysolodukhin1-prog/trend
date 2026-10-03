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

CREATE TABLE IF NOT EXISTS public.marketplace_questions (
    question_key text PRIMARY KEY,
    source_question_id text NOT NULL,
    marketplace text NOT NULL CHECK (marketplace = 'wb'),
    product_id text NOT NULL,
    seller_article text,
    product_name text,
    category_name text,
    brand text,
    question_date date,
    question_text text,
    answer_text text,
    answered boolean NOT NULL DEFAULT false,
    was_viewed boolean,
    source text NOT NULL DEFAULT 'wb_seller_api',
    source_synced_at timestamptz NOT NULL DEFAULT now(),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_marketplace_questions_date
    ON public.marketplace_questions (question_date DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_questions_product
    ON public.marketplace_questions (product_id, question_date DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_questions_unanswered
    ON public.marketplace_questions (question_date DESC)
    WHERE NOT answered;
