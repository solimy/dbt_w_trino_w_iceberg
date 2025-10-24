{{ config(materialized='view') }}

with s as (
    select * from {{ ref('stg_sales') }}
),

fx as (
    select
        base_cur,
        quote_cur,
        cast(rate as double) as rate
    from {{ ref('exchange_rates') }}
    where upper(quote_cur) = 'EUR'
)

select
    s.sold_at,
    s.store_id,
    s.country_code,
    s.country_name,
    s.currency                   as sale_currency,
    s.amount                     as amount_original,
    coalesce(f.rate, 1.0)        as fx_rate_to_eur,
    cast(s.amount * coalesce(f.rate, 1.0) as decimal(18,2)) as amount_eur
from s
left join fx f
  on upper(s.currency) = upper(f.base_cur);
