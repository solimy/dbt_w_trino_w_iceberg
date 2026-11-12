package system.authz

default allow := false

allow if {
	input.method == "POST"
	input.path in {
		["v1", "data", "trino", "allow"],
		["v1", "data", "trino", "rowFilters"],
		["v1", "data", "trino", "columnMask"],
	}
}

allow if {
	input.method == "GET"
	input.path == ["v1", "data", "platform", "revision"]
}
