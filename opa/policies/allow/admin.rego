package trino

allow if {
    input.context.identity.user == "admin"
}
