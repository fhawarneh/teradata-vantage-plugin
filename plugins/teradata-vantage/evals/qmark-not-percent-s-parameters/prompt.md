---
model: sonnet
max_turns: 6
timeout_seconds: 180
allowed_tools: [Skill]
---
I am porting this from psycopg to Teradata and it fails. Fix it and tell me why it failed.

    cur.execute(
        "SELECT customer_id, sale_amount FROM analytics.sales_fact WHERE region = %s",
        (region,),
    )
