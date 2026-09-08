-- Synthetic fixture: a clean Teradata query over placeholder objects (no real system referenced).
SELECT TOP 10
    c.region_name,
    COUNT(*)            AS order_count,
    SUM(f.sales_amount) AS total_sales
FROM sales_db.sales_fact AS f
JOIN sales_db.customer_dim AS c
    ON c.customer_id = f.customer_id
WHERE f.order_date >= DATE '2026-01-01'
  AND f.status_code IS NOT NULL
GROUP BY c.region_name
ORDER BY total_sales DESC;
