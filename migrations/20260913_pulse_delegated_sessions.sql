-- Explicit PULSE USER REGISTRY migration; never run from an HTTP request.
-- Apply after 20260912_pulse_personal_sessions.sql. No credentials are stored here.
BEGIN;
SET LOCAL lock_timeout='5s';
ALTER TABLE public.bi_personal_sessions
  ADD COLUMN IF NOT EXISTS parent_session_hash text
  REFERENCES public.bi_personal_sessions(session_hash) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION public.guard_bi_delegated_session() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE parent public.bi_personal_sessions%ROWTYPE;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF NEW.parent_session_hash IS DISTINCT FROM OLD.parent_session_hash THEN
      RAISE EXCEPTION 'source parent session is immutable' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.parent_session_hash IS NOT NULL THEN
    SELECT * INTO parent FROM public.bi_personal_sessions
      WHERE session_hash=NEW.parent_session_hash FOR SHARE;
    IF NOT FOUND OR parent.parent_session_hash IS NOT NULL
      OR NEW.session_hash=NEW.parent_session_hash
      OR parent.user_id<>NEW.user_id OR parent.user_revision<>NEW.user_revision
      OR parent.revoked_at IS NOT NULL OR parent.expires_at<=clock_timestamp()
      OR NEW.expires_at>parent.expires_at THEN
      RAISE EXCEPTION 'invalid source parent session' USING ERRCODE='42501';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_bi_delegated_session() FROM PUBLIC;
DROP TRIGGER IF EXISTS guard_bi_delegated_session ON public.bi_personal_sessions;
CREATE TRIGGER guard_bi_delegated_session BEFORE INSERT OR UPDATE
  ON public.bi_personal_sessions FOR EACH ROW
  EXECUTE FUNCTION public.guard_bi_delegated_session();
ALTER TABLE public.bi_personal_sessions ENABLE ALWAYS TRIGGER guard_bi_delegated_session;
CREATE INDEX IF NOT EXISTS bi_personal_sessions_parent
  ON public.bi_personal_sessions(parent_session_hash) WHERE parent_session_hash IS NOT NULL;
COMMIT;
