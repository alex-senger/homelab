# Full access for OIDC logins in Authelia group `openbao-admins`.
path "*" {
  capabilities = ["create", "read", "update", "patch", "delete", "list", "sudo"]
}
