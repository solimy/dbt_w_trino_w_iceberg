package trino

import rego.v1

default allow := false

default rowFilters := []

identity := input.context.identity
operation := input.action.operation
resource := input.action.resource

authenticated if {
	is_string(identity.user)
	identity.user != ""
}

has_group(groups) if {
	authenticated
	some group in object.get(identity, "groups", [])
	group in groups
}

provisioner if {
	authenticated
	identity.user in data.platform.admins
}

schema_key(relation) := key if {
	key := sprintf("%s.%s", [relation.catalogName, relation.schemaName])
	count(split(key, ".")) == 2
}

schema_policy(relation) := data.platform.schemas[schema_key(relation)]

writer(schema) if {
	authenticated
	identity.user in schema.writers
}

dbt_writer if {
	some schema in data.platform.schemas
	writer(schema)
}

dbt_metadata(table) if {
	table.catalogName == "system"
	table.schemaName == "metadata"
	table.tableName == "materialized_views"
}

can_read_table(table) if {
	dbt_writer
	dbt_metadata(table)
}

can_manage(schema) if {
	provisioner
	is_object(schema)
}

can_manage(schema) if writer(schema)

can_read_schema(schema) if can_manage(schema)

can_read_schema(schema) if has_group(schema.readers)

can_see_schema(schema) if can_read_schema(schema)

can_see_schema(schema) if {
	some output in schema.outputs
	has_group(output.readers)
}

can_see_catalog(catalog) if {
	some key, schema in data.platform.schemas
	split(key, ".")[0] == catalog
	can_see_schema(schema)
}

known_identity if provisioner

known_identity if {
	some schema in data.platform.schemas
	can_see_schema(schema)
}

ordinary_table(table) if {
	is_string(table.tableName)
	table.tableName != ""
	not contains(table.tableName, "$")
}

can_read_table(table) if {
	provisioner
	is_string(table.tableName)
	schema_policy(table)
}

can_read_table(table) if {
	ordinary_table(table)
	can_read_schema(schema_policy(table))
}

can_read_table(table) if {
	ordinary_table(table)
	output := schema_policy(table).outputs[table.tableName]
	has_group(output.readers)
}

# Trino applies the catalog, schema, table, and column filters to these rows.
can_read_table(table) if {
	table.schemaName == "information_schema"
	table.tableName in {"columns", "schemata", "tables"}
	can_see_catalog(table.catalogName)
}

can_see_columns(_) if provisioner

can_see_columns(table) if {
	every column in object.get(table, "columns", []) {
		not startswith(column, "$")
	}
}

allow if {
	operation == "ExecuteQuery"
	known_identity
}

allow if {
	operation in {"ViewQueryOwnedBy", "FilterViewQueryOwnedBy", "KillQueryOwnedBy"}
	known_identity
	resource.user.user == identity.user
}

allow if {
	operation == "SetSystemSessionProperty"
	known_identity
	resource.systemSessionProperty.name in {"query_max_run_time", "query_max_execution_time"}
}

allow if {
	operation in {"AccessCatalog", "FilterCatalogs", "ShowSchemas"}
	can_see_catalog(resource.catalog.name)
}

allow if {
	operation == "AccessCatalog"
	resource.catalog.name == "system"
	dbt_writer
}

allow if {
	operation in {"FilterSchemas", "ShowTables"}
	can_see_schema(schema_policy(resource.schema))
}

allow if {
	operation == "ShowCreateSchema"
	can_manage(schema_policy(resource.schema))
}

allow if {
	operation in {"FilterTables", "ShowColumns", "FilterColumns", "SelectFromColumns"}
	can_read_table(resource.table)
	can_see_columns(resource.table)
}

allow if {
	operation == "ExecuteFunction"
	known_identity
	resource.function.catalogName == "system"
	resource.function.schemaName == "builtin"
}

allow if {
	operation == "CreateViewWithExecuteFunction"
	some schema in data.platform.schemas
	can_manage(schema)
	resource.function.catalogName == "system"
	resource.function.schemaName == "builtin"
}

allow if {
	operation == "CreateSchema"
	provisioner
	schema_policy(resource.schema)
}

allow if {
	operation in {
		"AddColumn", "AlterColumn", "CreateView", "CreateViewWithSelectFromColumns",
		"DeleteFromTable", "DropColumn", "DropTable", "DropView", "InsertIntoTable",
		"RenameColumn", "SetColumnComment", "SetTableComment", "SetViewComment",
		"ShowCreateTable", "TruncateTable", "UpdateTableColumns",
	}
	ordinary_table(resource.table)
	can_manage(schema_policy(resource.table))
}

allow if {
	operation in {"CreateTable", "SetTableProperties"}
	ordinary_table(resource.table)
	can_manage(schema_policy(resource.table))
	every property in object.keys(object.get(resource.table, "properties", {})) {
		property in {"format", "format_version", "partitioning", "sorted_by"}
	}
}

allow if {
	operation in {"RenameTable", "RenameView"}
	source := resource.table
	target := input.action.targetResource.table
	ordinary_table(source)
	ordinary_table(target)
	source.catalogName == target.catalogName
	source.schemaName == target.schemaName
	can_manage(schema_policy(source))
	can_manage(schema_policy(target))
}

columnMask := {"expression": "NULL"} if {
	operation == "GetColumnMask"
	column := resource.column
	mask := schema_policy(column).outputs[column.tableName].masks[column.columnName]
	not has_group(mask.unmasked_groups)
}

rowFilters := [{"expression": concat(" OR ", clauses)}] if {
	dbt_metadata(resource.table)
	clauses := [sprintf("(catalog_name = '%s' AND schema_name = '%s')", split(key, ".")) |
		some key, schema in data.platform.schemas
		writer(schema)
	]
	count(clauses) > 0
}
