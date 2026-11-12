variable "platform" {
  description = "Declared team memberships and service clients for the data-platform realm."
  type = object({
    teams = map(object({
      maintainers = list(string)
      members     = list(string)
    }))
    clients = map(object({
      secret = string
      group  = string
    }))
  })
  sensitive = true
  nullable  = false

  validation {
    condition = alltrue([
      for team in keys(var.platform.teams) :
      team != "" && !strcontains(team, "/")
    ])
    error_message = "Team names must be nonempty and cannot contain a slash."
  }

  validation {
    condition = alltrue([
      for client_id, client in var.platform.clients :
      !contains(["trino", "platform-provisioner", "query-console"], client_id) && try(trimspace(client.secret) != "", false)
    ])
    error_message = "Each service client must have a secret and cannot use trino, platform-provisioner or query-console as its ID."
  }

  validation {
    condition = alltrue([
      for client in values(var.platform.clients) :
      client.group == null ? true : contains(flatten([
        for team in keys(var.platform.teams) : ["/teams/${team}/maintainers", "/teams/${team}/members"]
      ]), client.group)
    ])
    error_message = "A service client's group must be null or the path to a declared team's maintainers or members group."
  }
}

variable "trino_client_secret" {
  description = "Client secret for Trino's browser OAuth2 flow."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition     = trimspace(var.trino_client_secret) != ""
    error_message = "The Trino client secret must be set."
  }
}

variable "trino_url" {
  description = "Public Trino base URL used to build the Keycloak redirect URI."
  type        = string
  default     = "https://platform-trino.127.0.0.1.nip.io"
  nullable    = false
}
