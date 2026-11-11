-- READ ONLY. Bucket visibility, not receipt contents or object paths.
SELECT id AS bucket_id, public AS publicly_readable
FROM storage.buckets ORDER BY id;

-- Run separately. Policy definitions need review if clients have direct Storage access.
-- Do not share the full output if a policy contains personal identifiers.
SELECT policyname, roles, cmd, qual, with_check
FROM pg_policies WHERE schemaname='storage' AND tablename='objects'
ORDER BY policyname;
