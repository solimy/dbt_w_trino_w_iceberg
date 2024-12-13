select
    count(*) as airbyte_count
from {{ source('airbyte', 'airbyte_raw_data') }}
