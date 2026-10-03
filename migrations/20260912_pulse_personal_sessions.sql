-- Apply explicitly to the PULSE USER REGISTRY database, before the matching app release.
-- No application request creates this schema; missing ledger must fail closed.
BEGIN;
CREATE TABLE IF NOT EXISTS public.bi_personal_sessions (
    session_hash text PRIMARY KEY CHECK (session_hash ~ '^[0-9a-f]{64}$'),
    user_id bigint NOT NULL REFERENCES public.bi_users(user_id),
    user_revision timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS bi_personal_sessions_user ON public.bi_personal_sessions(user_id);
REVOKE ALL ON public.bi_personal_sessions FROM PUBLIC;
CREATE OR REPLACE FUNCTION public.guard_bi_personal_session() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'personal session deletion requires maintenance';
    END IF;
    IF NEW.session_hash IS DISTINCT FROM OLD.session_hash
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.user_revision IS DISTINCT FROM OLD.user_revision
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
    THEN
        RAISE EXCEPTION 'personal session identity and revocation are immutable';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS guard_bi_personal_session ON public.bi_personal_sessions;
CREATE TRIGGER guard_bi_personal_session BEFORE UPDATE OR DELETE
ON public.bi_personal_sessions FOR EACH ROW EXECUTE FUNCTION public.guard_bi_personal_session();
COMMIT;
