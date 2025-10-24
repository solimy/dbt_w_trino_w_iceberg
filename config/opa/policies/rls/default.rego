package trino
import future.keywords.in
import future.keywords.if
import future.keywords.contains

table_resource := input.action.resource.table

is_admin if {
    input.context.identity.user == "admin"
}

rowFilters contains {"expression": "user_type <> 'customer'"} if {
    not is_admin
    table_resource.catalogName == "sample_catalog"
    table_resource.schemaName == "sample_schema"
    table_resource.tableName == "restricted_table"
}