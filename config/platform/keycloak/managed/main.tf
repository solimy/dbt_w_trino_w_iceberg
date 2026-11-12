terraform {
  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "5.7.0"
    }
  }
}

provider "keycloak" {}

locals {
  realm_id = "data-platform"

  # Keep the client secrets out of resource instance keys.
  teams         = nonsensitive(var.platform.teams)
  client_groups = nonsensitive({ for client_id, client in var.platform.clients : client_id => client.group })
  grouped_clients = {
    for client_id, group_path in local.client_groups : client_id => group_path
    if group_path != null
  }

  group_memberships = merge(
    { for team, config in local.teams : "/teams/${team}/maintainers" => {
      team    = team
      name    = "maintainers"
      members = config.maintainers
    } },
    { for team, config in local.teams : "/teams/${team}/members" => {
      team    = team
      name    = "members"
      members = config.members
    } },
  )
}

resource "keycloak_group" "teams" {
  realm_id = local.realm_id
  name     = "teams"
}

resource "keycloak_group" "team" {
  for_each = local.teams

  realm_id  = local.realm_id
  parent_id = keycloak_group.teams.id
  name      = each.key
}

resource "keycloak_group" "team_role" {
  for_each = local.group_memberships

  realm_id  = local.realm_id
  parent_id = keycloak_group.team[each.value.team].id
  name      = each.value.name
}

data "keycloak_openid_client_service_account_user" "grouped_client" {
  for_each = local.grouped_clients

  realm_id  = local.realm_id
  client_id = keycloak_openid_client.service[each.key].id
}

# Reconcile human and service-account membership together for each team role.
resource "keycloak_group_memberships" "team_role" {
  for_each   = local.group_memberships
  depends_on = [keycloak_user.query_console]

  realm_id = local.realm_id
  group_id = keycloak_group.team_role[each.key].id
  members = concat(each.value.members, [
    for client_id, group_path in local.grouped_clients :
    data.keycloak_openid_client_service_account_user.grouped_client[client_id].username
    if group_path == each.key
  ])
}
