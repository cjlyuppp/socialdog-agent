UPDATE settings SET value=json_set(value, '$.allow_post_cutoff_reference_after_submit', json('false'))
WHERE json_type(value, '$.allow_post_cutoff_reference_after_submit') IS NULL;
