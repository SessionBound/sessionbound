# Credential-Token Tests

Raw file: `raw_results/credential_token_1783224156.json`.

| Test | Expected production property | Observed prototype behavior | Status |
|---|---|---|---|
| Credential A with token B mismatch | Denied when credential_id does not match the runtime principal | Denied | Pass |
| Token replay with second credential | Denied when a token is replayed with another credential | Denied | Pass |
| Expired token | Denied after token expiration | Denied | Pass |
| Wrong audience | Denied when token audience is not `sessionbounddb` | Denied | Pass |
| Wrong actor | Denied when token actor differs from the credential actor | Denied | Pass |
| Safe-view registry version drift | Denied after safe-view registry version changes | Denied | Pass |
| Safe-view policy version drift | Denied after safe-view policy version changes | Denied | Pass |
| View-definition hash mismatch | Denied when the token hash differs from the runtime safe-view snapshot | Denied | Pass |
| Revoked task state | Denied after task revocation | Denied | Pass |
| Second active task binding in same session | Denied if one session tries to bind a second active task | Denied | Pass |

The updated prototype validates signatures, token expiration, revoked task
state, credential-id binding, actor matching, audience validation,
cross-credential token replay, same-session rebind rejection, and
safe-view registry/hash/policy drift invalidation.
