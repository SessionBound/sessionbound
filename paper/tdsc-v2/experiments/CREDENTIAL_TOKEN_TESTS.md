# Credential-Token Tests

Raw file: `raw_results/credential_token_1783221767.json`.

| Test | Expected production property | Observed prototype behavior | Status |
|---|---|---|---|
| Credential A with token B mismatch | Denied if credential-token binding is implemented | Allowed | Gap |
| Token replay with second credential | Denied if token is bound to one credential/runtime principal | Allowed | Gap |
| Expired token | Denied after token expiration | Denied | Pass |
| Revoked task state | Denied after task revocation | Denied | Pass |
| Second active task binding in same session | Denied if one session can bind only one task | Allowed | Gap |

The current prototype validates signatures, token expiration, and revoked
task state. It does not yet implement strict credential-id binding,
single-credential token use, or same-session rebind rejection.
