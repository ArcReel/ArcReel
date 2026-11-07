# New assets (new_assets)

The asset table above lists all registered characters, scenes, and props, with aliases in parentheses. For assets needed on screen, first compare names, aliases, and descriptions; directly reference the registered name when already listed. For a character / scene / prop absent from the table that needs a fixed appearance, use a short designation at the reference and list an entry in `new_assets`; names listed in `new_assets` can be referenced just like registered names. List each same-type, same-name designation only once; use an empty array `[]` if there are no new assets. Products reference only registered product names and are not listed in `new_assets`.

Each entry contains `type`, `name` (the designation used at the reference), `decision`, and a one-sentence `reason` (the basis, such as “the protagonist from opening to ending”). `decision` is one of:

- `register`: register a new asset. Write appearance directly usable to generate an asset sheet in `description` (features and clothing for characters, space and furnishings for scenes, form and material for props); put other designations in `aliases`; use `asset_name` if a more formal registered name is needed.
- `merge`: another designation for a registered asset (for example, “older sister” refers to registered “Xiaomei”). `target` is a registered name of the same type or the designation of another new asset in this batch.
- `derivative`: characters only, another appearance of a registered character (such as a change of clothing). `target` is the registered base name, `asset_name` is the derivative name, and `description` contains only changes relative to the base.
- `skip`: a briefly appearing person or object that needs no fixed appearance; describe it in text without registering it.

`name` and `asset_name` are each a single name; `target` expresses the derivative relationship. Keep descriptions of registered assets unchanged; leave `description` empty for `merge` and `skip`.
