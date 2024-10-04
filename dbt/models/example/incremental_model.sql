
-- Use the `ref` function to select from other models
{{
    config(
        materialized='incremental',
        alias='incremental_model',
    )
}}

select current_timestamp(6) as run_time
