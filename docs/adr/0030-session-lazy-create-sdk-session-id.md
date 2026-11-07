---
status: accepted
---

# Create sessions on send; use sdk_session_id as the sole public identity

Precreating session rows requires generating IDs and maintaining a mapping once SDK session_id returns, and leaves created-but-never-used rows. Do not precreate: start the actor and first message in temporary memory, write the DB session only after SDK init returns session_id, and swap the memory key from temporary to real ID. Thereafter all query/update/resume operations use unique `sdk_session_id` as the sole public identity.

## Consequences

- Before the first message's SDK init returns, the session is not addressable: it is absent from lists and cannot be subscribed/resumed by ID.
- Key swap is sensitive: any ID lookup in the sent-but-not-initialized window must account for temporary IDs.
