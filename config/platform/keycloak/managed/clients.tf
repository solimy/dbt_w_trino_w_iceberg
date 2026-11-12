resource "keycloak_openid_client" "trino" {
  realm_id  = local.realm_id
  client_id = "trino"

  access_type                  = "CONFIDENTIAL"
  client_secret                = var.trino_client_secret
  standard_flow_enabled        = true
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  service_accounts_enabled     = false
  valid_redirect_uris          = ["${trimsuffix(var.trino_url, "/")}/oauth2/callback"]
}

resource "keycloak_openid_client" "service" {
  for_each = local.client_groups

  realm_id  = local.realm_id
  client_id = each.key

  access_type                  = "CONFIDENTIAL"
  client_secret                = var.platform.clients[each.key].secret
  standard_flow_enabled        = false
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  service_accounts_enabled     = true
}

resource "keycloak_openid_audience_protocol_mapper" "trino" {
  realm_id  = local.realm_id
  client_id = keycloak_openid_client.trino.id
  name      = "trino-audience"

  included_client_audience = keycloak_openid_client.trino.client_id
  add_to_access_token      = true
  add_to_id_token          = false
}

resource "keycloak_openid_audience_protocol_mapper" "service" {
  for_each = local.client_groups

  realm_id  = local.realm_id
  client_id = keycloak_openid_client.service[each.key].id
  name      = "trino-audience"

  included_client_audience = keycloak_openid_client.trino.client_id
  add_to_access_token      = true
  add_to_id_token          = false
}

resource "keycloak_openid_group_membership_protocol_mapper" "trino" {
  realm_id  = local.realm_id
  client_id = keycloak_openid_client.trino.id
  name      = "team-groups"

  claim_name          = "groups"
  full_path           = true
  add_to_access_token = true
  add_to_id_token     = true
  add_to_userinfo     = true
}

resource "keycloak_openid_group_membership_protocol_mapper" "service" {
  for_each = local.grouped_clients

  realm_id  = local.realm_id
  client_id = keycloak_openid_client.service[each.key].id
  name      = "team-groups"

  claim_name          = "groups"
  full_path           = true
  add_to_access_token = true
  add_to_id_token     = false
  add_to_userinfo     = false
}
