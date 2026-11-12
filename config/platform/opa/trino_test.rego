package trino_test

import rego.v1

import data.trino

provisioner := {"user": "service-account-platform-sql", "groups": []}
dev_writer := {"user": "service-account-dbt-ventes-commandes-dev", "groups": []}
prd_writer := {"user": "service-account-dbt-ventes-commandes-prd", "groups": []}
maintainer := {"user": "owner", "groups": ["/teams/commerce/maintainers"]}
consumer := {"user": "analyst", "groups": ["/teams/finance/members"]}
unmasked_consumer := {"user": "analyst", "groups": ["/teams/finance/members", "/teams/privacy/members"]}
outsider := {"user": "visitor", "groups": ["/teams/finance/members/extra", "finance", "/teams/Commerce/maintainers"]}

dev_schema := {"catalogName": "mydata_dev", "schemaName": "ventes__commandes_internal"}
prd_schema := {"catalogName": "mydata_prd", "schemaName": "ventes__commandes_public"}
dev_table := object.union(dev_schema, {"tableName": "stg_sales"})
prd_table := object.union(prd_schema, {"tableName": "mart_country_daily_sales_eur"})
prd_temp := object.union(prd_schema, {"tableName": "mart_country_daily_sales_eur__dbt_tmp"})

policy_fixture := {
	"admins": [provisioner.user],
	"schemas": {
		"mydata_dev.ventes__commandes_internal": {
			"writers": [dev_writer.user],
			"readers": ["/teams/commerce/maintainers"],
			"outputs": {},
		},
		"mydata_dev.ventes__commandes_public": {
			"writers": [dev_writer.user],
			"readers": [],
			"outputs": {},
		},
		"mydata_prd.ventes__commandes_public": {
			"writers": [prd_writer.user],
			"readers": [],
			"outputs": {"mart_country_daily_sales_eur": {
				"readers": ["/teams/finance/members"],
				"masks": {"country_name": {"unmasked_groups": []}},
			}},
		},
	},
}

request(identity, operation, resource) := {
	"context": {"identity": identity},
	"action": {"operation": operation, "resource": resource},
}

rename_request(identity, operation, source, target) := object.union(
	request(identity, operation, {"table": source}),
	{"action": {"targetResource": {"table": target}}},
)

test_consumer_can_query_and_discover_only_its_output if {
	every check in [
		request(consumer, "ExecuteQuery", {}),
		request(consumer, "AccessCatalog", {"catalog": {"name": "mydata_prd"}}),
		request(consumer, "FilterCatalogs", {"catalog": {"name": "mydata_prd"}}),
		request(consumer, "ShowSchemas", {"catalog": {"name": "mydata_prd"}}),
		request(consumer, "FilterSchemas", {"schema": prd_schema}),
		request(consumer, "ShowTables", {"schema": prd_schema}),
		request(consumer, "FilterTables", {"table": prd_table}),
		request(consumer, "ShowColumns", {"table": prd_table}),
		request(consumer, "FilterColumns", {"table": object.union(prd_table, {"columns": ["country_name"]})}),
		request(consumer, "SelectFromColumns", {"table": object.union(prd_table, {"columns": ["country_name"]})}),
	] {
		trino.allow with input as check with data.platform as policy_fixture
	}
	every check in [
		request(consumer, "AccessCatalog", {"catalog": {"name": "mydata_dev"}}),
		request(consumer, "FilterCatalogs", {"catalog": {"name": "mydata_dev"}}),
		request(consumer, "FilterSchemas", {"schema": dev_schema}),
		request(consumer, "FilterTables", {"table": prd_temp}),
		request(consumer, "FilterColumns", {"table": object.union(dev_table, {"columns": ["country_name"]})}),
		request(consumer, "SelectFromColumns", {"table": prd_temp}),
		request(consumer, "SelectFromColumns", {"table": dev_table}),
	] {
		not trino.allow with input as check with data.platform as policy_fixture
	}
}

test_group_match_requires_exact_path_and_case if {
	every check in [
		request(outsider, "ExecuteQuery", {}),
		request(outsider, "SelectFromColumns", {"table": prd_table}),
		request(outsider, "SelectFromColumns", {"table": dev_table}),
		request({"user": "/teams/finance/members", "groups": []}, "SelectFromColumns", {"table": prd_table}),
	] {
		not trino.allow with input as check with data.platform as policy_fixture
	}
}

test_maintainer_can_read_internal_but_cannot_write if {
	trino.allow with input as request(maintainer, "SelectFromColumns", {"table": dev_table}) with data.platform as policy_fixture
	not trino.allow with input as request(maintainer, "InsertIntoTable", {"table": dev_table}) with data.platform as policy_fixture
}

test_revocation_changes_authorization_and_discovery if {
	revoked := json.patch(policy_fixture, [{"op": "replace", "path": "/schemas/mydata_prd.ventes__commandes_public/outputs/mart_country_daily_sales_eur/readers", "value": []}])
	every check in [
		request(consumer, "FilterCatalogs", {"catalog": {"name": "mydata_prd"}}),
		request(consumer, "FilterSchemas", {"schema": prd_schema}),
		request(consumer, "FilterTables", {"table": prd_table}),
		request(consumer, "SelectFromColumns", {"table": prd_table}),
	] {
		trino.allow with input as check with data.platform as policy_fixture
		not trino.allow with input as check with data.platform as revoked
	}
}

test_provisioning_and_writer_operations_stay_in_managed_schemas if {
	every check in [
		request(provisioner, "CreateSchema", {"schema": prd_schema}),
		request(provisioner, "CreateTable", {"table": dev_table}),
		request(dev_writer, "CreateTable", {"table": dev_table}),
		request(dev_writer, "DropTable", {"table": dev_table}),
		request(dev_writer, "InsertIntoTable", {"table": dev_table}),
		request(dev_writer, "DeleteFromTable", {"table": dev_table}),
		request(dev_writer, "UpdateTableColumns", {"table": dev_table}),
		request(dev_writer, "TruncateTable", {"table": dev_table}),
		request(dev_writer, "CreateView", {"table": dev_table}),
		request(dev_writer, "CreateViewWithSelectFromColumns", {"table": dev_table}),
	] {
		trino.allow with input as check with data.platform as policy_fixture
	}
	every check in [
		request(provisioner, "CreateSchema", {"schema": object.union(prd_schema, {"schemaName": "unmanaged"})}),
		request(dev_writer, "CreateSchema", {"schema": dev_schema}),
		request(dev_writer, "CreateTable", {"table": prd_table}),
		request(dev_writer, "CreateTable", {"table": object.union(dev_table, {"properties": {"location": "s3://elsewhere/"}})}),
		request(prd_writer, "CreateViewWithSelectFromColumns", {"table": dev_table}),
		request(consumer, "DropTable", {"table": prd_table}),
	] {
		not trino.allow with input as check with data.platform as policy_fixture
	}
}

test_renames_require_source_and_target_in_the_same_writer_schema if {
	both_schema_writer := json.patch(policy_fixture, [{"op": "add", "path": "/schemas/mydata_prd.ventes__commandes_public/writers/-", "value": dev_writer.user}])
	dev_temp := object.union(dev_table, {"tableName": "stg_sales__dbt_tmp"})
	dev_public_table := object.union(dev_table, {"schemaName": "ventes__commandes_public"})
	every operation in ["RenameTable", "RenameView"] {
		trino.allow with input as rename_request(dev_writer, operation, dev_temp, dev_table) with data.platform as policy_fixture
		not trino.allow with input as rename_request(dev_writer, operation, dev_temp, dev_public_table) with data.platform as policy_fixture
		not trino.allow with input as rename_request(dev_writer, operation, dev_public_table, dev_table) with data.platform as policy_fixture
		not trino.allow with input as rename_request(dev_writer, operation, dev_temp, prd_table) with data.platform as both_schema_writer
		not trino.allow with input as rename_request(dev_writer, operation, prd_table, dev_table) with data.platform as both_schema_writer
		not trino.allow with input as request(dev_writer, operation, {"table": dev_temp}) with data.platform as policy_fixture
	}
}

test_session_and_query_metadata_permissions_are_narrow if {
	trino.allow with input as request(consumer, "SetSystemSessionProperty", {"systemSessionProperty": {"name": "query_max_run_time"}})
		with data.platform as policy_fixture
	not trino.allow with input as request(consumer, "SetSystemSessionProperty", {"systemSessionProperty": {"name": "path"}})
		with data.platform as policy_fixture
	every operation in ["ViewQueryOwnedBy", "FilterViewQueryOwnedBy", "KillQueryOwnedBy"] {
		trino.allow with input as request(consumer, operation, {"user": consumer}) with data.platform as policy_fixture
		not trino.allow with input as request(consumer, operation, {"user": maintainer}) with data.platform as policy_fixture
	}
}

test_dbts_can_use_metadata_and_builtin_functions if {
	info_table := {"catalogName": "mydata_dev", "schemaName": "information_schema", "tableName": "tables"}
	every check in [
		request(dev_writer, "SelectFromColumns", {"table": info_table}),
		request(dev_writer, "ExecuteFunction", {"function": {"catalogName": "system", "schemaName": "builtin", "functionName": "count"}}),
	] {
		trino.allow with input as check with data.platform as policy_fixture
	}
	not trino.allow with input as request(consumer, "SelectFromColumns", {"table": info_table}) with data.platform as policy_fixture
}

test_privileged_operations_are_denied_by_default if {
	every identity in [provisioner, dev_writer, consumer] {
		every operation in [
			"ImpersonateUser", "GrantTablePrivilege", "SetTableAuthorization", "ReadSystemInformation",
			"ExecuteProcedure", "ExecuteTableProcedure", "CreateFunction", "CreateCatalog",
		] {
			not trino.allow with input as request(identity, operation, {"table": prd_table}) with data.platform as policy_fixture
		}
	}
	not trino.allow with input as request(consumer, "ExecuteFunction", {"function": {"catalogName": "mydata_prd", "schemaName": "system", "functionName": "query"}})
		with data.platform as policy_fixture
}

test_metadata_tables_and_hidden_columns_cannot_bypass_mask if {
	every check in [
		request(consumer, "SelectFromColumns", {"table": object.union(prd_table, {"tableName": "mart_country_daily_sales_eur$files"})}),
		request(prd_writer, "SelectFromColumns", {"table": object.union(prd_table, {"tableName": "mart_country_daily_sales_eur$files"})}),
		request(consumer, "SelectFromColumns", {"table": object.union(prd_table, {"columns": ["$path"]})}),
		request(consumer, "FilterColumns", {"table": object.union(prd_table, {"columns": ["$path"]})}),
	] {
		not trino.allow with input as check with data.platform as policy_fixture
	}
}

test_mask_applies_until_explicitly_unmasked if {
	column := object.union(prd_table, {"columnName": "country_name", "columnType": "varchar"})
	mask_request := request(consumer, "GetColumnMask", {"column": column})
	trino.columnMask == {"expression": "NULL"} with input as mask_request with data.platform as policy_fixture
	trino.columnMask == {"expression": "NULL"} with input as request(provisioner, "GetColumnMask", {"column": column}) with data.platform as policy_fixture
	trino.columnMask == {"expression": "NULL"} with input as request(prd_writer, "GetColumnMask", {"column": column}) with data.platform as policy_fixture
	not trino.columnMask with input as request(consumer, "GetColumnMask", {"column": object.union(column, {"columnName": "sales_eur"})}) with data.platform as policy_fixture
	exception := json.patch(policy_fixture, [{"op": "add", "path": "/schemas/mydata_prd.ventes__commandes_public/outputs/mart_country_daily_sales_eur/masks/country_name/unmasked_groups/-", "value": "/teams/privacy/members"}])
	trino.columnMask == {"expression": "NULL"} with input as mask_request with data.platform as exception
	not trino.columnMask with input as request(unmasked_consumer, "GetColumnMask", {"column": column}) with data.platform as exception
	trino.rowFilters == [] with input as request(consumer, "GetRowFilters", {"table": prd_table}) with data.platform as policy_fixture
}

test_dbt_system_metadata_is_limited_to_writer_schemas if {
	table := {"catalogName": "system", "schemaName": "metadata", "tableName": "materialized_views"}
	trino.allow with input as request(dev_writer, "AccessCatalog", {"catalog": {"name": "system"}}) with data.platform as policy_fixture
	trino.allow with input as request(dev_writer, "SelectFromColumns", {"table": table}) with data.platform as policy_fixture
	not trino.allow with input as request(consumer, "SelectFromColumns", {"table": table}) with data.platform as policy_fixture
	not trino.allow with input as request(dev_writer, "SelectFromColumns", {"table": object.union(table, {"tableName": "catalogs"})}) with data.platform as policy_fixture
	filters := trino.rowFilters with input as request(dev_writer, "GetRowFilters", {"table": table}) with data.platform as policy_fixture
	count(filters) == 1
	contains(filters[0].expression, "ventes__commandes_internal")
	not contains(filters[0].expression, "mydata_prd")
}
