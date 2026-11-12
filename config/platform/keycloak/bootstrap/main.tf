terraform {
  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "5.7.0"
    }
  }
}

provider "keycloak" {}

variable "provisioner_secret" {
  description = "Client secret for the realm-scoped provisioner."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition     = trimspace(var.provisioner_secret) != ""
    error_message = "The provisioner client secret must be set."
  }
}

resource "keycloak_realm" "platform" {
  realm                 = "data-platform"
  enabled               = true
  ssl_required          = "external"
  access_token_lifespan = "5m"
}

data "keycloak_openid_client" "realm_management" {
  realm_id  = keycloak_realm.platform.id
  client_id = "realm-management"
}

resource "keycloak_openid_client" "provisioner" {
  realm_id  = keycloak_realm.platform.id
  client_id = "platform-provisioner"

  access_type                  = "CONFIDENTIAL"
  client_secret                = var.provisioner_secret
  standard_flow_enabled        = false
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  service_accounts_enabled     = true
}

resource "keycloak_openid_client_service_account_role" "provisioner" {
  realm_id                = keycloak_realm.platform.id
  service_account_user_id = keycloak_openid_client.provisioner.service_account_user_id
  client_id               = data.keycloak_openid_client.realm_management.id
  role                    = "realm-admin"
}
