with eur_to_any as (
    select
        quote_cur as currency,
        rate as rate
    from {{ ref('exchange_rates') }}
    where base_cur = 'EUR'
), any_to_eur as (
    select
        currency,
        1 / rate as rate
    from eur_to_any
), any_to_any as (
    select
        a.currency as from_currency,
        b.currency as to_currency,
        b.rate / a.rate as rate
    from eur_to_any a
    cross join eur_to_any b
)

select *
from any_to_any
