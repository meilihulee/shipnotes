## llm 0.36

### New
- Added new models gpt-6-sol and gpt-6-luna.

### Improved
- Improved SQLite connection management for enhanced stability.
- Now wraps reasoning traces in <details><summary>.
- Now keeps tool parameters named title in generated input_schema. ([#1680](https://github.com/simonw/llm/pull/1680))
- Updated model.supports_conversation property. ([#1692](https://github.com/simonw/llm/pull/1692))
- Improved the re.split function to only use positional arguments in the future.

### Fixed
- Fixed default model options when constructing prompts directly. ([#1663](https://github.com/simonw/llm/pull/1663))
- Fixed AsyncResponse.log_to_db. ([#1668](https://github.com/simonw/llm/pull/1668))
- Fixed supports_schema attribute name in advanced model plugins docs. ([#1686](https://github.com/simonw/llm/pull/1686))
- Fixed an error in the provider generator. ([#1659](https://github.com/simonw/llm/pull/1659))
- Fixed a KeyError 'options_json' in the 'llm logs -t' markdown output. ([#1656](https://github.com/simonw/llm/pull/1656))

**What's new:** llm 0.36 is out. This release introduces new models gpt-6-sol and gpt-6-luna, along with improved prompt handling and enhanced stability across various features. We’ve also addressed some minor bugs and optimized the system for better performance.
