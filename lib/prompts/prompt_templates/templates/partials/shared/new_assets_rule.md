# New assets in this episode (new_assets)

The asset table above lists all registered assets, with aliases in parentheses. For assets appearing in this episode’s source, first identify them against the names, aliases, and descriptions in the table; if recognized, directly reference the registered name. For an unrecognized character / scene / prop, use its original source designation at the reference and list an entry for it in `new_assets`; names listed in `new_assets` can be referenced just like registered names. List each same-type, same-name designation only once; use an empty array `[]` if there are no new assets.

Each entry contains `type`, `name` (the designation used at the reference), `decision`, and a one-sentence `reason` (the basis, such as “first appears in paragraph 3 and has dialogue”). `decision` is one of:

- `register`: register a new asset. Write appearance in `description` based on the source (features and clothing for characters, space and furnishings for scenes, form and material for props); put other source designations in `aliases`; use `asset_name` if a more formal registered name is needed.
- `merge`: another designation for a registered asset (for example, “the general” refers to registered “Pei Yu”). `target` is a registered name of the same type or the designation of another new asset in this batch; after confirmation, the designation becomes an alias of that asset.
- `derivative`: characters only, another appearance of a registered character (such as youth or a change of clothing). `target` is the registered base name, `asset_name` is the derivative name (such as “young”), and `description` contains only changes relative to the base.
- `skip`: a one-off person or object that needs no fixed appearance; describe it in text without registering it after confirmation.

`name` and `asset_name` are each a single name; `target` expresses the derivative relationship. Keep descriptions of registered assets unchanged; leave `description` empty for `merge` and `skip`.
