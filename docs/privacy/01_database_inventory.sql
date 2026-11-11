-- READ ONLY. Run each numbered SELECT separately in Supabase SQL Editor.
-- No ALTER, GRANT, REVOKE, INSERT, UPDATE or DELETE is performed.

-- 1. SQL Editor role. This describes the SQL Editor connection, NOT necessarily
-- your backend connection; compare with the backend role you already checked.
SELECT current_user AS database_role, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = current_user;

-- 2. Public table inventory, RLS status and browser-role privileges.
-- Privilege=true does NOT alone mean exposed data: RLS policies also apply.
-- A sensitive table with RLS=false and effective browser-role access is a blocker.
SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       (SELECT count(*) FROM pg_policies p
        WHERE p.schemaname='public' AND p.tablename=c.relname) AS policy_count,
       has_table_privilege('anon',c.oid,'SELECT') AS anon_select,
       has_table_privilege('authenticated',c.oid,'SELECT') AS authenticated_select,
       has_table_privilege('anon',c.oid,'INSERT,UPDATE,DELETE') AS anon_any_write,
       has_table_privilege('authenticated',c.oid,'INSERT,UPDATE,DELETE') AS authenticated_any_write
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p')
ORDER BY c.relname;

-- 3. View/function exposure needs review too; RLS on base tables alone is not enough.
SELECT c.relname AS view_name, c.relkind,
       has_table_privilege('anon',c.oid,'SELECT') AS anon_select,
       has_table_privilege('authenticated',c.oid,'SELECT') AS authenticated_select,
       c.reloptions
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('v','m')
ORDER BY c.relname;

-- 4. Public functions callable by browser roles. Listing is not proof of a vulnerability.
-- Review DEFINER functions and the actual behaviour before changing permissions.
SELECT p.proname AS function_name, p.prosecdef AS security_definer,
       has_function_privilege('anon',p.oid,'EXECUTE') AS anon_execute,
       has_function_privilege('authenticated',p.oid,'EXECUTE') AS authenticated_execute
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.prokind='f'
ORDER BY p.proname;
