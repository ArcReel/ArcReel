## ADDED Requirements

### Requirement: Clue Thumbnail Stack Display

The SegmentCard header SHALL display image thumbnails for clues associated with the current storyboard (`clues_in_segment` / `clues_in_scene`) to the left of the character avatar stack. The shape is rounded square, the stacking style is consistent with the character avatar stack, a maximum of 4 are shown, and overflow beyond that is represented by a `+n` overflow badge.

#### Scenario: Display Thumbnail When Clue Has an Image

- **WHEN** the clue object has a `clue_sheet` path
- **THEN** the corresponding image is displayed in a rounded square shape (`rounded`) with dimensions consistent with the character avatar (`h-7 w-7`)

#### Scenario: Display Initial Letter Placeholder When Clue Has No Image

- **WHEN** the clue object does not have a `clue_sheet` path
- **THEN** a color block with the clue name's initial letter is displayed (rounded square); the color is determined by a hash of the name, consistent with the character avatar fallback rules

#### Scenario: No Rendering When Storyboard Has No Associated Clues

- **WHEN** the storyboard's `clues_in_segment` / `clues_in_scene` is an empty array
- **THEN** the clue thumbnail stack is not rendered; only the character avatar stack is displayed on the right side of the header

#### Scenario: Show Overflow Count When More Than 4 Clues

- **WHEN** the storyboard has more than 4 associated clues
- **THEN** only the first 4 thumbnails are displayed, with the remaining count shown as a gray `+n` badge

### Requirement: Clue Hover Popover

When the mouse hovers over a clue thumbnail, a popover SHALL appear displaying the clue image, name, type tag (Location/Prop), and a description summary, with a layout consistent with the character popover.

#### Scenario: Hover Shows Clue Details

- **WHEN** the user hovers the mouse over a clue thumbnail
- **THEN** a popover appears showing the clue image on the left (icon placeholder if no image) and the clue name with a one-line description summary on the right

#### Scenario: Popover Shows Location Type Tag

- **WHEN** the popover is displayed and the clue `type` is `"location"`
- **THEN** a "Location" tag (amber color) is displayed next to the name

#### Scenario: Popover Shows Prop Type Tag

- **WHEN** the popover is displayed and the clue `type` is `"prop"`
- **THEN** a "Prop" tag (emerald color) is displayed next to the name

### Requirement: Character Popover Adds "Character" Type Tag

AvatarPopover SHALL add a "Character" type tag next to the character name, unified in style with the clue popover's tags, to facilitate distinguishing between the two entity types.

#### Scenario: Hover Character Avatar Shows "Character" Tag

- **WHEN** the user hovers the mouse over a character avatar
- **THEN** a "Character" tag (indigo color) is displayed next to the character name in the popover
