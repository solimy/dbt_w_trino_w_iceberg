package trino
import future.keywords.in
import future.keywords.if
import future.keywords.contains

column_resource := input.action.resource.column
is_admin if {
    input.context.identity.user == "admin"
}

columnMask := {"expression": "NULL"} if {
    not is_admin
    column_resource.catalogName == "sample_catalog"
    column_resource.schemaName == "sample_schema"
    column_resource.tableName == "restricted_table"
    column_resource.columnName == "user_phone"
}

columnMask := {"expression": "'****' || substring(user_name, -3)"} if {
    not is_admin
    column_resource.catalogName == "sample_catalog"
    column_resource.schemaName == "sample_schema"
    column_resource.tableName == "restricted_table"
    column_resource.columnName == "user_name"
}