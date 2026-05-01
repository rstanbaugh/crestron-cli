---
policy:
  purpose: "Reusable agent behavior guidance for Python CLI projects"
  scope: "General rules only; keep project/domain-specific behavior in design/requirements/spec docs"

defaults:
  language: python
  cli_framework: argparse
  package_entrypoint_pattern: "<package>.main:cli"
  runtime_dependencies: project_defined
  environment: dedicated_conda

core_philosophy:
  - simplicity_first
  - clarity_and_consistency_over_cleverness
  - optimize_for_humans_and_automation_agents
  - explicit_over_implicit

non_negotiables:
  - use_project_conda_env_for_install_run_test
  - install_packages_only_in_project_environment
  - never_install_into_system_or_conda_base_without_explicit_user_approval
  - do_not_assume_backward_compatibility
  - confirm_breaking_change_policy_first
  - never_commit_secrets_or_credential_files
  - keep_runtime_and_cache_artifacts_untracked_unless_required

interaction_rules:
  - ask_clarifying_questions_when_instructions_are_ambiguous
  - share_short_plan_for_non_trivial_changes
  - summarize_what_changed_after_user_feedback
  - state_tradeoffs_explicitly
  - confirm_direction_when_multiple_reasonable_options_exist

testing_and_design:
  runner: pytest
  rules:
    - test_conditions_are_part_of_design
    - add_or_update_tests_for_new_functionality
    - add_regression_test_for_bug_fixes
    - update_tests_with_code_and_docs_when_behavior_changes
    - prefer_small_focused_tests
    - keep_tests_deterministic

core_design_rules:
  default_output: human_readable_table_when_appropriate
  flags_on_relevant_commands:
    - --json
    - --yaml
    - --raw
    - --refresh
  terminology: consistent_across_commands_help_and_output_contracts

  # CLI Entrypoint Rule
  - always provide a top-level script (e.g., toolname.py) as the entrypoint for CLI tools, which imports and runs the main function from the package/module. Do not require users to set or modify PYTHONPATH. Avoid python -m for user-facing CLI wrappers; match the pattern used in crestron-cli and tempest-cli.

help_behavior:
  supports:
    - -h
    - --help
    - help
  command_level:
    - "command -h"
    - "command --help"
    - missing_required_arguments_shows_command_help
  subcommand_level:
    - "command subcommand -h"
    - "command subcommand --help"
    - "command subcommand help"
  requirements:
    - document_supported_forms
    - cover_supported_forms_with_tests

configuration_precedence:
  - cli_flags
  - environment_variables
  - dotenv_file
  - defaults

structured_output_contract:
  stable_fields:
    success: boolean
    message: human_readable_success_message
    error: short_error_description_when_success_false
    details: optional_structured_context
    data: primary_payload
  rules:
    - treat_structured_output_as_api_contract
    - avoid_silent_contract_changes
    - update_docs_and_tests_with_contract_changes

documentation_sync:
  - keep_project_specific_rules_in_project_docs
  - update_docs_in_same_change_set_when_behavior_or_contract_changes

security_and_repo_hygiene:
  - sanitize_exported_tooling_artifacts_before_commit
  - if_history_contains_secrets_rewrite_and_force_push_before_public_release

idempotency:
  - commands_should_be_safe_to_rerun_when_possible
  - side_effects_must_be_explicit_in_help_text

logging:
  stdout: primary_command_output
  stderr: errors_and_diagnostics
  structured_output:
    modes:
      - --json
      - --yaml
      - --raw
    rules:
      - stdout_only
      - no_mixed_human_text

exit_codes:
  note: recommended_unix_style_for_new_projects_or_when_touching_error_handling
  map:
    0: success
    1: general_error
    2: invalid_arguments_or_usage
  custom_codes: avoid_unless_explicitly_documented

change_safety_checklist:
  - run_syntax_checks_and_pytest_in_project_environment
  - run_at_least_one_cli_smoke_check_for_changed_command_paths
  - confirm_new_or_changed_functionality_has_test_coverage
  - confirm_help_text_reflects_actual_grammar
  - confirm_docs_and_tests_are_aligned
  - confirm_git_status_has_no_sensitive_artifacts
---

# AGENTS.md

This file uses YAML policy metadata for machine readability.
Keep project/domain-specific behavior in design/requirements/spec documents.
