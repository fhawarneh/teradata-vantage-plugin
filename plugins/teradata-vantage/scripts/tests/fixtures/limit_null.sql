-- Synthetic fixture: two dialect slips the lint hook should surface (LIMIT, = NULL).
SELECT customer_id, status_code
FROM sales_db.customer_dim
WHERE status_code = NULL
ORDER BY customer_id
LIMIT 10;
