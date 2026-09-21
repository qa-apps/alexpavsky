-- ============================================================
-- ops/cleanup_logs.sql — 90-day chat log retention
--
-- Run daily via system cron:
--   0 3 * * * psql $DATABASE_URL -f /path/to/ops/cleanup_logs.sql >> /var/log/pg_cleanup.log 2>&1
--
-- Or with explicit URL:
--   0 3 * * * psql "postgresql://alex:PASSWORD@localhost:5432/alexpavsky" \
--       -f /home/alex/alexpavsky/ops/cleanup_logs.sql >> /var/log/pg_cleanup.log 2>&1
--
-- To enable inside PostgreSQL via pg_cron instead:
--   CREATE EXTENSION IF NOT EXISTS pg_cron;
--   SELECT cron.schedule(
--       'cleanup-chat-logs',
--       '0 3 * * *',
--       $$DELETE FROM chat_logs WHERE created_at < NOW() - INTERVAL '90 days';$$
--   );
-- ============================================================

\echo 'cleanup_logs.sql — starting at ' :current_timestamp

DELETE FROM chat_logs
WHERE created_at < NOW() - INTERVAL '90 days';

-- Optional: also clean up expired sessions older than 90 days
DELETE FROM sessions
WHERE expires_at < NOW() - INTERVAL '90 days';

-- Optional: clean up used/expired password reset tokens older than 7 days
DELETE FROM password_resets
WHERE used = TRUE
  AND created_at < NOW() - INTERVAL '7 days';

\echo 'cleanup_logs.sql — done'
