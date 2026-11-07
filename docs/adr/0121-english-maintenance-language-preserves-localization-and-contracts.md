---
status: accepted
---

# English maintenance prose preserves localization and runtime contracts

The approved maintenance translation makes English the language of repository-owned internal documentation, development instructions, comments, docstrings, built-in prompts and Agent instructions. ArcReel also contains localized product copy, Chinese-first public documentation, user-authored content and literals consumed by executable code. Treating every Chinese string as prose would change contracts and behavior.

## Decision

Classify each relevant span by its consumers before translating it:

- Translate explanatory maintenance prose into English, preserving its complete meaning, conditions, exceptions and examples.
- Review prompts, tool/field descriptions, skill triggers and normative examples as behavioral instructions. Their translators are not their sole semantic reviewers.
- Preserve literal identities, structure, defaults, enums, numeric limits, commands and syntax recognized by code. Keep historical compatibility data and executable migrations stable, including indirect dependencies on current templates.
- Preserve user content, customizations, memory, history, licenses, third-party notices, localized resources and multilingual test inputs. Record justified exceptions alongside the inventory.

English maintenance prose includes built-in prompt/template metadata, even where a settings view displays that source metadata. Keep the product's supported locales, locale selection, fallbacks and generated-content language policies unchanged.

Chinese writing guidance in `docs/standards/copy.md` and `.claude/rules/copy-writing.md` governs remaining Chinese passages. This decision supersedes the former blanket requirement to write internal Agent documentation in Chinese; explicit output-language requirements in existing workflows remain effective. Keep the glossary's Chinese product labels available for Chinese copy while using its canonical domain concepts in English maintenance prose.

Public documentation keeps its existing Chinese-source-to-English translation pipeline, locale URLs and public editions. Translate the English public edition through that pipeline rather than changing its source direction.

## Delivery and compatibility

Translate sources, regenerate derived indexes/copies through their owning generators, and review machine contracts separately from descriptions. Preserve multilingual evaluation inputs and review each changed textual expectation against its producer; assertions and snapshots are not weakened to obtain a passing run.

Existing project profiles use the existing manifest-based synchronization rules. Preserve profile IDs and schema versions; retain customized files, deletions and collisions instead of forcing a reset. Existing personalized profiles may therefore contain both languages. Source reversal must also be verified after profile synchronization.

Treat existing artifact validity and exact provider-request equivalence separately. Translated rendered prose may change request fingerprints while formal-content bases remain identical; preserve frozen submitted requests for resume. Paid regeneration is outside this maintenance translation.

Local tests and bilingual source review establish bounded evidence. Behavioral release of translated prompts requires the separately defined real-model comparisons, with a selected model, environment and budget. Keep incomplete translation, independent review and evaluation visible rather than declaring success from a character scan.

## Consequences

Maintenance instructions become consistent in English while product localization and existing content retain their contracts. Some Chinese remains intentionally as localized data, syntax, historical compatibility values or source examples. New translation batches require consumer tracing and independent review, and finite tests cannot prove universal model equivalence.

The fixed source baseline, evidence, exceptions, remaining work and evaluation/rollback procedures are recorded in [the translation implementation record](../plans/english-translation/README.md).
