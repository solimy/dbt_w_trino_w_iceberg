variable "query_console_url" {
  type        = string
  description = "Public base URL of the SQL console."
}

variable "query_console_secret" {
  type        = string
  sensitive   = true
  description = "OIDC client secret of the SQL console."
}

variable "query_console_users" {
  type = map(object({
    email      = string
    first_name = string
    last_name  = string
    password   = string
    temporary  = bool
  }))
  sensitive   = true
  default     = {}
  description = "Local Keycloak users and their initial passwords."
}

resource "keycloak_openid_client" "query_console" {
  realm_id                     = local.realm_id
  client_id                    = "query-console"
  access_type                  = "CONFIDENTIAL"
  client_secret                = var.query_console_secret
  standard_flow_enabled        = true
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  service_accounts_enabled     = false
  pkce_code_challenge_method   = "S256"
  valid_redirect_uris          = ["${var.query_console_url}/oauth/callback"]
}

resource "keycloak_openid_audience_protocol_mapper" "query_console" {
  realm_id                 = local.realm_id
  client_id                = keycloak_openid_client.query_console.id
  name                     = "trino-audience"
  included_client_audience = keycloak_openid_client.trino.client_id
  add_to_access_token      = true
  add_to_id_token          = false
}

resource "keycloak_openid_group_membership_protocol_mapper" "query_console" {
  realm_id            = local.realm_id
  client_id           = keycloak_openid_client.query_console.id
  name                = "team-groups"
  claim_name          = "groups"
  full_path           = true
  add_to_access_token = true
  add_to_id_token     = false
  add_to_userinfo     = false
}

resource "keycloak_user" "query_console" {
  for_each       = nonsensitive(toset(keys(var.query_console_users)))
  realm_id       = local.realm_id
  username       = each.key
  enabled        = true
  email          = var.query_console_users[each.key].email
  email_verified = !var.query_console_users[each.key].temporary
  first_name     = var.query_console_users[each.key].first_name
  last_name      = var.query_console_users[each.key].last_name

  initial_password {
    value     = var.query_console_users[each.key].password
    temporary = var.query_console_users[each.key].temporary
  }
}
