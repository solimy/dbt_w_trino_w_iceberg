{{ config(materialized='view') }}

with raw as (
    select
        cast(datetime as timestamp(6))           as sold_at,
        cast(amount as double)                   as amount,
        upper(currency)                          as currency,
        case
            when upper(country) = 'UK' then 'GB'
            else upper(country)
        end                                      as country_code,
        cast(store_id as integer)                as store_id
    from {{ ref('sales') }}
),

with_country as (
    select
        r.*,
        c.country_name,
        c.currency as country_currency
    from raw r
    left join {{ ref('countries') }} c
      on r.country_code = c.country_code
)

select * from with_country;
