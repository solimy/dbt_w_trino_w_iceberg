# Motivations

In the previous story, we saw how we could use DBT with Trino and Iceberg. We saw that we could use DBT to transform our data and Trino to query it. 

To do some tests, I used the dbt seed command to create some data. Unfortunately, dbt seeds currently only support CSV files. I wanted to use something else, like json (ok in the end I used CSV because I already had a setup for that, but json and any other source would have worked as well).