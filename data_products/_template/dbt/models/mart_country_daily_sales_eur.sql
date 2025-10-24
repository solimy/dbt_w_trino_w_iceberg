{{ config(materialized='view') }}

with f as (
    select * from {{ ref('fct_sales_eur') }}
)

select
    extract(year from sold_at) as sales_year,
    extract(month from sold_at) as sales_month,
    country_code,
    country_name,
    cast(sum(amount_eur) as decimal(18,2)) as total_sales_eur,
    count(*) as transactions
from f
group by 1, 2, 3, 4
order by sales_year, sales_month, country_code;
