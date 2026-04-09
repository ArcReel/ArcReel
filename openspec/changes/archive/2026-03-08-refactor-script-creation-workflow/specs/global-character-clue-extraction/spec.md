## ADDED Requirements

### Requirement: Character/Clue Extraction Must Support Full-Book Analysis Mode

The `analyze-characters-clues` subagent SHALL support analyzing the entire novel and extracting all characters and clues at once.

#### Scenario: Analyze the Entire Novel
- **WHEN** the subagent is dispatched without specifying an analysis range
- **THEN** the subagent reads all novel text under `projects/{project_name}/source/`, extracts all characters and clues, and writes them to project.json

#### Scenario: Analyze a Specified Chapter Range
- **WHEN** the subagent is dispatched with a specified analysis range (e.g., "Chapters 1-3" or "a specific file")
- **THEN** the subagent analyzes only the text in the specified range, extracting characters and clues from that range

### Requirement: Character/Clue Extraction Must Support Incremental Append Mode

When project.json already has characters/clues, the subagent SHALL compare against existing data and only append newly discovered characters and clues, without overwriting existing definitions.

#### Scenario: Append New Characters When Character List Already Exists
- **WHEN** project.json already has 5 character definitions and the subagent discovers 3 new characters during analysis
- **THEN** the subagent only appends the 3 new characters to project.json, leaving the original 5 unchanged

#### Scenario: Existing Character Descriptions Are Not Overwritten
- **WHEN** a character in project.json already has a manually modified description or character_sheet
- **THEN** the subagent does not overwrite that character's existing data, only noting "already exists, skipped" in the returned summary

### Requirement: Extracted Character Descriptions Must Comply With Image Generation Specifications

Extracted character descriptions SHALL contain only visual information that can be directly used for image generation.

#### Scenario: Character Description Contains Only Visual Elements
- **WHEN** the subagent extracts character information
- **THEN** the description field contains appearance highlights, clothing, distinctive features, color keywords, and reference style; it does NOT include personality descriptions, character relationships, or plot background (non-visual information)

#### Scenario: voice_style Is Recorded Separately
- **WHEN** the novel contains descriptions of a character's voice/tone
- **THEN** the subagent records the voice information in the voice_style field (for later dubbing reference), separate from visual descriptions

### Requirement: Clue Extraction Must Distinguish Type and Importance

Extracted clues SHALL be tagged with type (location/prop) and importance (major/minor).

#### Scenario: Scene-Type Clues Tagged as location
- **WHEN** the clue is an environment/scene (e.g., "deep in a bamboo forest", "inn lobby")
- **THEN** the clue type is tagged as "location" with a description including spatial structure and lighting atmosphere

#### Scenario: Prop-Type Clues Tagged as prop
- **WHEN** the clue is an object/prop (e.g., "jade pendant", "letter")
- **THEN** the clue type is tagged as "prop" with a description including size reference, material, and appearance details

#### Scenario: Important Clues Tagged as major
- **WHEN** a clue appears repeatedly in the plot or plays a key role
- **THEN** the clue importance is tagged as "major" (a design image will be generated for it later)

#### Scenario: Minor Clues Tagged as minor
- **WHEN** a clue appears only occasionally or as background decoration
- **THEN** the clue importance is tagged as "minor" (only the description is retained; no design image is generated)

### Requirement: Extraction Results Must Pass Data Validation

After the subagent writes to project.json, it SHALL call data validation to ensure integrity.

#### Scenario: Call validate_project for Validation
- **WHEN** the subagent completes writing characters/clues
- **THEN** the subagent calls `validate_project(project_name)` to validate the project.json structure and reference integrity

#### Scenario: Fix Data When Validation Fails
- **WHEN** validate_project returns a validation failure
- **THEN** the subagent fixes the data based on the error information and re-validates until it passes

### Requirement: Subagent Must Return a Structured Summary

The result returned by the `analyze-characters-clues` subagent to the main agent SHALL be a concise structured summary, not containing raw novel text.

#### Scenario: Return Character Summary
- **WHEN** the subagent completes character extraction
- **THEN** the returned content contains: number of newly added characters, list of character names, and a one-sentence description of each character

#### Scenario: Return Clue Summary
- **WHEN** the subagent completes clue extraction
- **THEN** the returned content contains: number of newly added clues, major/minor distribution, and a list of clue names and types
