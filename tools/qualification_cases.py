"""Reviewed case assertions; absent entries stay not_run, regardless of other test counts."""

MADDY_TESTS = "tests.maddy.test_real_maddy.RealMaddyTests"
VALIDATION_TESTS = "tests.unit.test_validation.ValidationTests"
SETUP_TESTS = "tests.integration.test_setup.SetupApiTests"
RUNTIME_TESTS = "tests.runtime.test_container.RuntimeTests"
AUTHORIZATION_TESTS = "tests.integration.test_authorization_matrix.AuthorizationMatrixTests"
SECURITY_TESTS = "tests.integration.test_security.SecurityApiTests"
AUTHENTICATION_TESTS = "tests.unit.test_authentication.AuthenticationTests"
API_TESTS = "tests.integration.test_api.ApiTests"
SMTP_TESTS = "tests.integration.test_smtp_delivery.SmtpDeliveryTests"
HOST_TESTS = "tests.host.test_helper_lifecycle.HelperLifecycleTests"
DASHBOARD_HOST_TESTS = "tests.host.test_dashboard_lifecycle.DashboardLifecycleTests"
PERMISSIONS_TESTS = "tests.maddy.test_permissions_sessions.PermissionsAndSessionsTests"
PROTOCOL_TESTS = "tests.maddy.test_helper_protocol.RealHelperProtocolTests"
BOUNDARY_TESTS = "tests.maddy.test_linux_boundary.LinuxBoundaryTests"
MAILBOX_VALIDATION_TESTS = "tests.integration.test_mailbox_validation.MailboxValidationApiTests"
PROCESS_TESTS = "tests.unit.test_process.ProcessBoundsTests"
INVENTORY_TESTS = "tests.maddy.test_inventory_metadata.InventoryAndMetadataTests"
PROVISIONING_TESTS = "tests.maddy.test_provisioning_failures.ProvisioningFailureTests"
RECOVERY_TESTS = "tests.maddy.test_recovery.RecoveryTests"
CERTBOT_TESTS = 'tests.acme.test_certbot_issuance.CertbotIssuanceTests'
TLS_CONSUMER_TESTS = 'tests.acme.test_consumer_activation.ConsumerActivationTests'
READINESS_TESTS = 'tests.integration.test_relay_readiness.RelayReadinessApiTests'
STORAGE_FAILURE_TESTS = 'tests.integration.test_storage_failures.StorageFailureApiTests'
NETWORK_DEADLINE_TESTS = 'tests.integration.test_network_deadlines.NetworkDeadlineApiTests'
HOUSEKEEPING_TESTS = 'tests.unit.test_housekeeping.HousekeepingTests'
GUARDED_DELETION_TESTS = 'tests.maddy.test_guarded_deletion.GuardedDeletionTests'
ACME_COORDINATION_TESTS = 'tests.acme.test_coordination.AcmeCoordinationTests'
SNAPSHOT_CAPTURE_TEST = (
    'tests.acme.test_snapshot_capture.ActualSnapshotCaptureTests.'
    'test_both_real_writers_stop_and_only_previously_running_services_resume'
)
LOCK_EXCLUSION_TESTS = 'tests.unit.test_coordination_exclusion.CoordinationExclusionTests'
KERNEL_TESTS = 'tests.host.test_kernel_scenarios.KernelScenarioTests'
CUTOVER_RUNTIME_TESTS = 'tests.host.test_cutover_runtime.CutoverRuntimeTests'
CUTOVER_BOOT_TEST = (
    'tests.host.test_cutover_reboot.CutoverRebootTests.'
    'test_timers_stay_disabled_before_and_during_cutover_across_a_real_kernel_boot'
)
CUTOVER_SERVICE_TEST = (
    f'{CUTOVER_RUNTIME_TESTS}.'
    'test_loaded_service_takes_both_locks_and_missed_timer_runs_follow_served_checks'
)
MAINTENANCE_TESTS = 'tests.maddy.test_maintenance_coordination.MaintenanceCoordinationTests'
GENERATION_CRASH_TESTS = 'tests.acme.test_generation_crashes.GenerationCrashTests'

HOST_CASE_TESTS: dict[str, tuple[str, ...]] = {
    'T18.02': (
        'tests.host.test_replacement_recovery.ReplacementRecoveryTests.'
        'test_actual_immutable_off_vm_snapshot_is_verified_before_publication_and_download',
    ),
    'T18.03': (
        'tests.host.test_replacement_recovery.ReplacementRecoveryTests.'
        'test_complete_replacement_has_real_keys_boot_journal_tls_acme_mail_and_https',
    ),
    'T18.04': (
        'tests.host.test_replacement_recovery.ReplacementRecoveryTests.'
        'test_off_source_key_decrypts_the_snapshot_and_selected_recovery_objectives_hold',
    ),
    'T05.05': (
        f'{KERNEL_TESTS}.'
        'test_interrupted_offline_recovery_survives_kernel_boot_without_renewal_restarting_web',
    ),
    'T15.03': (
        f'{KERNEL_TESTS}.'
        'test_kernel_power_cut_at_all_staging_publication_and_consumer_boundaries_recovers',
    ),
    'T15.06': (
        f'{KERNEL_TESTS}.'
        'test_referenced_and_rollback_pairs_survive_retention_and_an_actual_kernel_boot',
    ),
    'T16.01': (
        'tests.host.test_legacy_drain.LegacyDrainTests.'
        'test_real_managed_legacy_writer_drains_before_baseline_scope_can_change_files',
        'tests.host.test_legacy_drain.LegacyDrainTests.'
        'test_real_manual_writer_leaves_completion_unknown_and_blocks_the_baseline_after_exit',
    ),
    'T16.02': (CUTOVER_BOOT_TEST,),
    'T16.03': (
        f'{CUTOVER_RUNTIME_TESTS}.'
        'test_every_real_forward_file_and_activation_transition_requires_matched_rollback',
        f'{CUTOVER_RUNTIME_TESTS}.'
        'test_every_interrupted_real_rollback_stays_paused_until_its_matched_retry_is_verified',
        CUTOVER_BOOT_TEST,
    ),
    'T16.04': (CUTOVER_SERVICE_TEST,),
    'T16.05': (CUTOVER_SERVICE_TEST,),
    "T08.01": (
        f"{DASHBOARD_HOST_TESTS}."
        "test_helper_starts_late_preserving_authorization_and_completed_mutation",
    ),
    "T08.02": (
        f"{HOST_TESTS}."
        "test_helper_restart_replaces_only_the_socket_and_preserves_the_bound_directory",
    ),
    "T08.03": (
        f"{HOST_TESTS}.test_docker_restart_reconnects_without_repeating_a_completed_mutation",
        "tests.host.test_dependency_recovery.UnitDependencyRecoveryTests."
        "test_recovered_docker_dependency_starts_the_helper_after_an_initial_failure",
    ),
    "T08.04": (
        f"{DASHBOARD_HOST_TESTS}."
        "test_full_reboot_prepares_restricted_paths_before_autostart_and_preserves_outcomes",
    ),
    'T22.02': (
        'tests.host.test_log_retention.HostLogRetentionTests.'
        'test_shipped_journal_limit_expires_old_records_and_keeps_current_records',
    ),
}

CASE_TESTS: dict[str, tuple[str, ...]] = {
    'T05.05': (
        f'{MAINTENANCE_TESTS}.'
        'test_interruption_keeps_actual_web_closed_against_renewal_and_unverified_resume',
    ),
    'T15.03': (
        f'{GENERATION_CRASH_TESTS}.'
        'test_sigkill_at_each_staging_and_publication_boundary_keeps_one_complete_pair',
        f'{GENERATION_CRASH_TESTS}.'
        'test_sigkill_between_actual_consumer_activations_resumes_without_another_issuance',
    ),
    'T15.06': (
        f'{GENERATION_CRASH_TESTS}.'
        'test_retention_protects_references_and_restarted_consumers_serve_the_active_pair',
    ),
    'T16.05': (
        f'{MAINTENANCE_TESTS}.'
        'test_first_dashboard_registration_cannot_race_real_renewal_activation',
    ),
    'T01.01': (
        'tests.integration.test_rollout_gates.RolloutGateTests.'
        'test_failed_build_or_recovery_assertion_blocks_all_dependent_maintenance_gates',
        'tests.unit.test_cutover.CutoverAdapterTests.'
        'test_gate_or_approval_failure_prevents_all_cutover_host_actions',
    ),
    'T01.02': (
        'tests.integration.test_rollout_gates.RolloutGateTests.'
        'test_every_missing_or_invalid_deployment_input_is_rejected',
        'tests.unit.test_rollout.RolloutTests.'
        'test_missing_deployment_identities_never_match_null_approval',
    ),
    'T01.03': (
        'tests.maddy.test_baseline_sources.BaselineSourceTests.'
        'test_initial_install_and_running_or_stopped_upgrades_enforce_actual_state_sources',
        'tests.maddy.test_restic_restore.NativeResticRestoreTests.'
        'test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository',
    ),
    'T01.04': (
        'tests.integration.test_rollout_gates.RolloutGateTests.'
        'test_skipped_failed_missing_and_reversed_phase_gates_cannot_be_used',
        'tests.browser.test_https.HttpsBrowserTests.'
        'test_fresh_bootstrap_precedes_startup_and_enrollment_follows_lock_release',
    ),
    'T01.05': (
        'tests.unit.test_qualification_adapters.QualificationAdapterTests.'
        'test_authenticated_metadata_and_negotiated_key_are_observed_not_expected_copies',
        'tests.unit.test_qualification_adapters.QualificationAdapterTests.'
        'test_running_image_is_compared_with_archive_contents_and_replacement_is_rejected',
        'tests.unit.test_qualification_adapters.QualificationAdapterTests.'
        'test_helper_or_unit_content_permissions_and_extra_source_drift_fail',
        'tests.unit.test_qualification_adapters.QualificationAdapterTests.'
        'test_effective_configuration_drift_is_type_sensitive',
    ),
    'T03.04': (
        'tests.browser.test_https.HttpsBrowserTests.'
        'test_secrets_are_masked_no_store_and_absent_from_browser_and_application_logs',
    ),
    'T03.05': (
        'tests.browser.test_https.HttpsBrowserTests.'
        'test_untrusted_names_and_notes_are_inert_and_actual_api_errors_have_only_safe_ids',
    ),
    'T18.02': (
        'tests.integration.test_r2_transfer.R2TransferTests.'
        'test_immutable_staging_and_manifest_publication_follow_actual_verified_https_transfer',
        'tests.integration.test_r2_transfer.R2TransferTests.'
        'test_failed_download_verification_never_publishes_a_trusted_manifest',
        'tests.integration.test_r2_transfer.R2TransferTests.test_existing_archive_cannot_be_overwritten',
    ),
    'T19.05': (
        'tests.maddy.test_maintenance_coordination.MaintenanceCoordinationTests.'
        'test_real_terminal_recovery_activates_after_both_shared_locks_are_released',
        'tests.unit.test_host_recovery.HostRecoveryTests.'
        'test_parallel_recovery_cannot_enter_the_gap_before_container_creation',
        'tests.unit.test_host_recovery.HostRecoveryTests.'
        'test_resume_keeps_the_private_lease_during_activation_without_self_deadlock',
        'tests.maddy.test_surviving_helper.SurvivingHelperTests.'
        'test_sigkill_parent_keeps_actual_cli_survivor_and_blocks_backup_and_duplicate_writes',
        'tests.maddy.test_restic_reconciliation.ResticReconciliationTests.'
        'test_a_real_surviving_process_blocks_reconciliation_until_repository_verification',
        'tests.maddy.test_restic_restore.NativeResticRestoreTests.'
        'test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository',
    ),
    'T21.04': (
        'tests.acme.test_resource_peak.ResourcePeakTests.'
        'test_combined_peak_includes_tmpfs_helper_cli_and_transient_work_within_budget',
    ),
    "T02.01": (f"{SETUP_TESTS}.test_missing_wrong_expired_and_reused_tokens_are_rejected",),
    "T02.02": (
        f"{SETUP_TESTS}.test_wrong_six_digit_totp_does_not_initialize_or_issue_recovery_codes",
    ),
    "T02.03": (
        f"{SETUP_TESTS}.test_competing_setup_requests_create_exactly_one_initialized_admin",
        "tests.unit.test_enrollment_otp.EnrollmentOtpTests."
        "test_bootstrap_wrong_expired_invalid_factor_and_competing_confirmations",
    ),
    "T02.04": (
        f"{RUNTIME_TESTS}.test_missing_initialized_database_cannot_reopen_setup_on_restart",
        f"{RUNTIME_TESTS}.test_wrong_or_missing_marker_cannot_reopen_setup_on_restart",
        "tests.unit.test_state.StateTests.test_missing_wrong_marker_and_wrong_schema_fail_closed",
    ),
    "T03.01": (
        f"{AUTHORIZATION_TESTS}."
        "test_invalid_sessions_deny_all_protected_routes",
        f"{RUNTIME_TESTS}.test_real_https_enrollment_cookie_helper_and_deletion_capability",
    ),
    "T03.02": (
        f"{AUTHORIZATION_TESTS}."
        "test_invalid_host_origin_and_csrf_context_cannot_mutate_with_a_valid_cookie",
        "tests.integration.test_api.ApiTests."
        "test_host_origin_and_validation_errors_have_no_side_effects_or_secrets",
    ),
    "T03.03": (
        f"{AUTHORIZATION_TESTS}."
        "test_reauthentication_binds_session_purpose_expiry_without_extending_session",
        "tests.unit.test_authentication.AuthenticationTests."
        "test_session_bound_reauth_and_expiration_preserve_absolute_expiry",
        "tests.unit.test_authentication.AuthenticationTests.test_wrong_purpose_and_session_binding_fail",
    ),
    "T03.06": (
        "tests.integration.test_api.ApiTests.test_unknown_api_never_falls_back_to_frontend_html",
        f"{RUNTIME_TESTS}.test_unknown_api_returns_safe_json_over_actual_https",
    ),
    "T04.02": (
        "tests.unit.test_authentication.AuthenticationTests."
        "test_parallel_recovery_consumption_occurs_exactly_once",
        "tests.unit.test_authentication.AuthenticationTests."
        "test_totp_and_email_verification_consume_each_value_once_under_concurrency",
    ),
    "T04.01": (
        "tests.unit.test_enrollment_otp.EnrollmentOtpTests."
        "test_incorrect_cross_challenge_and_expired_email_values_cannot_issue_a_session",
        "tests.unit.test_enrollment_otp.EnrollmentOtpTests.test_otp_replay_resend_and_challenge_binding",
        "tests.unit.test_enrollment_otp.EnrollmentOtpTests."
        "test_resend_resamples_a_colliding_value_and_only_the_new_code_works",
        "tests.unit.test_enrollment_otp.EnrollmentOtpTests.test_five_attempt_limit_is_persistent",
        "tests.unit.test_authentication.AuthenticationTests."
        "test_totp_window_and_watermark_never_move_backward",
        "tests.unit.test_authentication.AuthenticationTests."
        "test_parallel_recovery_consumption_occurs_exactly_once",
    ),
    "T04.03": (
        "tests.unit.test_authentication.AuthenticationTests."
        "test_recovery_regeneration_uses_128_bit_material_and_invalidates_the_old_set",
    ),
    "T04.04": (
        "tests.integration.test_budgets.AuthenticationBudgetApiTests."
        "test_peer_budget_shares_routes_ignores_forwarded_addresses_and_survives_restart",
        "tests.integration.test_budgets.AuthenticationBudgetApiTests."
        "test_thirty_admin_attempts_share_real_peers_and_new_addresses_cannot_reset_them",
    ),
    "T04.07": (
        "tests.integration.test_budgets.AuthenticationBudgetApiTests."
        "test_peer_budget_shares_routes_ignores_forwarded_addresses_and_survives_restart",
        "tests.integration.test_budgets.AuthenticationBudgetApiTests."
        "test_thirty_admin_attempts_share_real_peers_and_new_addresses_cannot_reset_them",
        "tests.unit.test_budgets.PersistentBudgetTests."
        "test_parallel_authentication_reservations_admit_exactly_thirty_admin_or_fifteen_peer",
    ),
    "T04.08": (
        "tests.unit.test_budgets.SharedSendBudgetTests."
        "test_email_otp_and_explicit_delivery_share_three_peer_and_five_admin_slots",
        "tests.unit.test_budgets.PersistentBudgetTests."
        "test_parallel_send_reservations_admit_exactly_five_admin_or_three_peer",
        "tests.unit.test_enrollment_otp.EnrollmentOtpTests.test_otp_replay_resend_and_challenge_binding",
    ),
    "T04.05": (
        f"{VALIDATION_TESTS}."
        "test_master_password_character_and_utf8_bounds_preserve_spaces_and_unicode",
        f"{AUTHORIZATION_TESTS}."
        "test_master_password_bounds_and_exact_unicode_spaces_survive_real_hashing",
    ),
    'T04.06': (
        f'{SETUP_TESTS}.'
        'test_setup_totp_step_cannot_be_reused_to_issue_a_login_session',
        f'{SECURITY_TESTS}.'
        'test_replacement_preserves_and_advances_the_totp_replay_watermark',
    ),
    "T04.09": (
        "tests.unit.test_authentication.AuthenticationTests."
        "test_exact_totp_digits_step_window_and_monotonic_consumption",
        "tests.unit.test_authentication.AuthenticationTests."
        "test_totp_window_and_watermark_never_move_backward",
    ),
    "T05.01": (f"{SECURITY_TESTS}.test_replacement_requires_password_and_an_existing_factor",),
    "T05.02": (
        f"{SECURITY_TESTS}.test_invalid_and_expired_replacement_preserve_the_active_factor",
    ),
    "T05.03": (
        f"{SECURITY_TESTS}.test_replacement_revokes_old_authentication_and_recovery_material",
    ),
    'T05.04': (
        'tests.maddy.test_restic_restore.NativeResticRestoreTests.'
        'test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository',
        'tests.unit.test_host_recovery.RecoveryImageTests.'
        'test_nonterminal_recovery_performs_no_docker_action',
    ),
    "T06.01": (
        f"{API_TESTS}.test_full_login_cookie_flags_and_logout",
        f"{RUNTIME_TESTS}.test_real_https_enrollment_cookie_helper_and_deletion_capability",
        f"{AUTHENTICATION_TESTS}."
        "test_idle_expiry_uses_the_last_activity_and_expires_at_exactly_fifteen_minutes",
        f"{AUTHENTICATION_TESTS}."
        "test_twelve_hour_absolute_expiry_cannot_be_extended_by_activity",
    ),
    "T06.02": (
        f"{API_TESTS}.test_full_login_cookie_flags_and_logout",
        f"{SECURITY_TESTS}.test_replacement_revokes_old_authentication_and_recovery_material",
        f"{SECURITY_TESTS}."
        "test_password_change_invalidates_all_pending_state_and_allows_fresh_login",
        f"{AUTHENTICATION_TESTS}."
        "test_recovery_regeneration_uses_128_bit_material_and_invalidates_the_old_set",
    ),
    "T06.03": (
        f"{SECURITY_TESTS}."
        "test_password_change_invalidates_all_pending_state_and_allows_fresh_login",
        f"{SECURITY_TESTS}.test_failed_password_write_rolls_back_authentication_invalidation",
    ),
    "T06.04": (
        f"{AUTHENTICATION_TESTS}."
        "test_concurrent_old_factor_verification_cannot_issue_a_session_after_password_change",
        f"{AUTHENTICATION_TESTS}."
        "test_delayed_old_password_worker_cannot_create_new_revision_challenge",
    ),
    'T06.05': (
        'tests.runtime.test_secret_keys.RuntimeSecretKeyTests.'
        'test_key_bytes_identity_and_sessions_survive_actual_container_recreation',
        'tests.runtime.test_secret_keys.RuntimeSecretKeyTests.'
        'test_missing_wrong_and_unreadable_keys_refuse_startup_without_generating_replacements',
    ),
    "T07.01": (
        f"{PROTOCOL_TESTS}."
        "test_invalid_schema_address_and_password_requests_never_execute_real_cli",
        f"{MAILBOX_VALIDATION_TESTS}."
        "test_http_boundary_rejects_the_same_address_and_password_values_before_any_cli",
    ),
    "T07.02": (
        f"{PROTOCOL_TESTS}."
        "test_invalid_schema_address_and_password_requests_never_execute_real_cli",
        f"{PROTOCOL_TESTS}."
        "test_normalized_legacy_address_and_exact_password_use_fixed_argv_and_stdin",
        f"{MAILBOX_VALIDATION_TESTS}."
        "test_http_boundary_rejects_the_same_address_and_password_values_before_any_cli",
        f"{MADDY_TESTS}."
        "test_utf8_72_byte_password_keeps_spaces_and_73_bytes_cannot_reach_cli",
    ),
    "T07.03": (
        f"{PROTOCOL_TESTS}."
        "test_invalid_schema_address_and_password_requests_never_execute_real_cli",
        f"{PROTOCOL_TESTS}."
        "test_normalized_legacy_address_and_exact_password_use_fixed_argv_and_stdin",
        f"{PROCESS_TESTS}."
        "test_actual_nonzero_exit_is_preserved_without_inheriting_parent_secrets",
    ),
    "T07.04": (
        f"{BOUNDARY_TESTS}.test_oversized_request_fails_before_any_cli_work",
        f"{BOUNDARY_TESTS}.test_partial_request_times_out_without_work_and_the_worker_recovers",
        f"{BOUNDARY_TESTS}."
        "test_four_real_idle_connections_fill_the_queue_and_excess_is_refused",
        f"{PROCESS_TESTS}.test_real_process_accepts_exactly_64_kib_and_rejects_larger_output",
        f"{PROCESS_TESTS}.test_real_slow_process_is_killed_and_completion_is_never_invented",
    ),
    "T07.05": (
        f"{PROTOCOL_TESTS}."
        "test_request_conflicts_and_unauthorized_peer_cannot_repeat_a_completed_mutation",
        f"{PROTOCOL_TESTS}."
        "test_host_status_redacts_nested_records_and_tolerates_corrupt_observation_time",
        f"{BOUNDARY_TESTS}."
        "test_service_uid_allowed_and_other_group_member_denied_by_peer_credentials",
    ),
    "T07.06": (
        f"{PROTOCOL_TESTS}."
        "test_missing_malformed_or_disabled_root_deletion_capability_prevents_all_cli",
        f"{MAILBOX_VALIDATION_TESTS}."
        "test_disabled_capabilities_refuse_deletion_and_email_even_with_confirmed_relay",
        "tests.unit.test_helper.HelperTests."
        "test_root_deletion_capability_requires_a_mapping_and_exact_true",
    ),
    "T09.01": (
        f"{PERMISSIONS_TESTS}."
        "test_host_service_uid_and_nobody_cannot_read_real_databases_or_delivered_messages",
        f"{MADDY_TESTS}.test_04_nobody_denied_and_daemon_launcher_umask_is_0077",
    ),
    "T09.02": (
        f"{PERMISSIONS_TESTS}.test_host_umask_does_not_control_an_unwrapped_container_exec",
    ),
    "T09.03": (
        f"{PERMISSIONS_TESTS}.test_daemon_and_a_real_waiting_cli_process_both_have_umask_0077",
        "tests.unit.test_helper.HelperTests.test_fixed_argv_and_password_only_on_stdin",
    ),
    "T09.04": (
        f"{PERMISSIONS_TESTS}.test_delivery_imap_writes_and_recreated_wal_files_remain_restricted",
    ),
    "T10.01": (
        f"{MADDY_TESTS}.test_01_eight_baseline_pairs_and_real_delivery",
        f"{INVENTORY_TESTS}.test_eight_real_pairs_and_both_partial_inventories_preserve_metadata",
    ),
    "T10.02": (
        f"{INVENTORY_TESTS}."
        "test_create_conflicts_with_either_store_without_granting_orphaned_mail_access",
    ),
    "T10.03": (
        f"{INVENTORY_TESTS}."
        "test_duplicate_and_fresh_create_preserve_actual_password_messages_and_metadata",
        f"{PROTOCOL_TESTS}."
        "test_request_conflicts_and_unauthorized_peer_cannot_repeat_a_completed_mutation",
    ),
    "T10.04": (
        f"{INVENTORY_TESTS}."
        "test_real_metadata_commit_failure_preserves_mail_and_retry_uses_completed_journal",
        f"{PROVISIONING_TESTS}."
        "test_created_storage_and_lost_cli_response_keep_access_mail_and_journal",
    ),
    "T11.01": (
        f"{MADDY_TESTS}.test_03_raw_password_upsert_and_helper_existing_only_guard",
    ),
    "T11.02": (
        f"{MADDY_TESTS}.test_03_raw_password_upsert_and_helper_existing_only_guard",
        f"{INVENTORY_TESTS}."
        "test_stale_reset_waits_for_deletion_lock_and_cannot_reinsert_missing_credentials",
        f"{INVENTORY_TESTS}."
        "test_reset_rejects_each_partial_inventory_before_any_password_upsert",
    ),
    "T11.03": (
        f"{VALIDATION_TESTS}.test_byte_bounds_and_exact_password_preservation",
        f"{MADDY_TESTS}."
        "test_utf8_72_byte_password_keeps_spaces_and_73_bytes_cannot_reach_cli",
    ),
    "T11.04": (
        f"{VALIDATION_TESTS}.test_byte_bounds_and_exact_password_preservation",
        f"{MADDY_TESTS}."
        "test_utf8_72_byte_password_keeps_spaces_and_73_bytes_cannot_reach_cli",
    ),
    "T12.01": (
        f"{PROVISIONING_TESTS}."
        "test_created_storage_and_lost_cli_response_keep_access_mail_and_journal",
        f"{MADDY_TESTS}.test_real_special_folder_warning_keeps_created_storage_and_credentials",
    ),
    "T12.02": (
        f"{MADDY_TESTS}.test_real_special_folder_warning_keeps_created_storage_and_credentials",
    ),
    'T12.03': (
        'tests.maddy.test_mutation_crashes.MutationCrashTests.'
        'test_sigkill_create_intent_each_cli_progress_observation_and_completion',
        'tests.maddy.test_mutation_crashes.MutationCrashTests.'
        'test_sigkill_reset_preserves_durable_outcome_and_never_repeats_password_change',
        'tests.maddy.test_mutation_crashes.MutationCrashTests.'
        'test_sigkill_delete_never_retries_removal_or_restores_access_automatically',
        'tests.maddy.test_mutation_crashes.MutationCrashTests.'
        'test_sigkill_credential_rollback_never_turns_unknown_completion_into_retry',
        'tests.runtime.test_api_crashes.ApiCrashTests.'
        'test_sigkill_https_during_create_retains_helper_completion_and_retry_commits_metadata',
        'tests.runtime.test_api_crashes.ApiCrashTests.'
        'test_sigkill_https_during_reset_never_repeats_password_change_or_removes_mail',
        'tests.runtime.test_api_crashes.ApiCrashTests.'
        'test_sigkill_https_during_authorized_fixture_delete_never_repeats_removal',
        f'{INVENTORY_TESTS}.'
        'test_real_metadata_commit_failure_preserves_mail_and_retry_uses_completed_journal',
    ),
    "T12.04": (
        f"{PROVISIONING_TESTS}."
        "test_known_failed_storage_creation_rolls_back_only_owned_new_credentials",
        f"{PROVISIONING_TESTS}."
        "test_created_storage_and_lost_cli_response_keep_access_mail_and_journal",
        f"{PROVISIONING_TESTS}.test_unknown_postconditions_never_authorize_credential_rollback",
        f"{INVENTORY_TESTS}."
        "test_create_conflicts_with_either_store_without_granting_orphaned_mail_access",
    ),
    "T12.05": (
        f"{RECOVERY_TESTS}.test_encrypted_restore_then_partial_body_repair_preserves_newer_mail",
        f"{RECOVERY_TESTS}.test_real_commit_failure_after_body_removal_keeps_a_durable_warning",
    ),
    "T12.06": (
        "tests.maddy.test_recovery.RecoveryTests."
        "test_real_commit_failure_after_body_removal_keeps_a_durable_warning",
    ),
    "T13.01": (
        f"{MADDY_TESTS}.test_02_reset_keeps_established_session_and_rejects_old_new_login",
    ),
    "T13.02": (
        f"{MADDY_TESTS}.test_02_reset_keeps_established_session_and_rejects_old_new_login",
        f"{PERMISSIONS_TESTS}."
        "test_credential_removal_keeps_old_session_until_restart_without_deleting_mail",
    ),
    "T13.03": (
        f"{PERMISSIONS_TESTS}."
        "test_credential_removal_keeps_old_session_until_restart_without_deleting_mail",
    ),
    "T14.01": (
        f"{SMTP_TESTS}."
        "test_real_starttls_delivery_needs_the_exact_received_identifier_and_saved_revision",
        f"{SMTP_TESTS}.test_real_implicit_tls_auth_sender_and_data_failures_do_not_enable_otp",
        f"{SMTP_TESTS}.test_real_hostname_validation_failure_sends_neither_authentication_nor_message",
        f"{SMTP_TESTS}.test_real_expired_certificate_sends_neither_authentication_nor_message",
        f"{SMTP_TESTS}.test_real_untrusted_certificate_sends_neither_authentication_nor_message",
        f"{SMTP_TESTS}.test_unavailable_smtp_peer_cannot_confirm_a_receipt_or_enable_otp",
    ),
    "T14.02": (
        "tests.unit.test_relay.RelayTests.test_failed_wrong_revision_and_unknown_tests_cannot_confirm",
        f"{SMTP_TESTS}."
        "test_real_starttls_delivery_needs_the_exact_received_identifier_and_saved_revision",
    ),
    "T14.03": (
        f"{SMTP_TESTS}."
        "test_real_starttls_delivery_needs_the_exact_received_identifier_and_saved_revision",
        "tests.unit.test_relay.RelayTests.test_mailbox_sender_test_does_not_enable_global_otp",
    ),
    'T14.04': (
        f'{READINESS_TESTS}.'
        'test_each_sender_needs_its_own_receipt_without_asserting_inbound_or_global_otp',
    ),
    'T14.05': (
        f'{READINESS_TESTS}.test_saved_credential_revision_revokes_all_previous_sender_receipts',
        f'{READINESS_TESTS}.'
        'test_resaving_after_manual_sender_domain_changes_requires_fresh_receipts',
        f'{READINESS_TESTS}.test_closed_restore_revokes_both_previously_confirmed_sender_receipts',
    ),
    'T14.06': (
        f'{READINESS_TESTS}.'
        'test_polling_sends_nothing_and_excess_explicit_sends_do_not_reach_smtp_or_logs',
    ),
    'T17.01': (
        f'{GUARDED_DELETION_TESTS}.'
        'test_restored_backup_requires_age_incarnation_and_complete_store_coverage',
        f'{GUARDED_DELETION_TESTS}.'
        'test_https_delete_updates_metadata_and_old_backup_cannot_cover_recreated_mail',
        f'{GUARDED_DELETION_TESTS}.'
        'test_pending_https_delete_resolves_once_and_old_backup_cannot_cover_recreated_mail',
    ),
    'T17.02': (
        f'{GUARDED_DELETION_TESTS}.'
        'test_unavailable_or_tampered_archive_and_forged_cutoff_cannot_authorize_real_deletion',
    ),
    'T17.03': (
        f'{GUARDED_DELETION_TESTS}.'
        'test_missing_stale_changed_or_protected_recipient_policy_blocks_actual_removal',
    ),
    'T17.04': (
        'tests.maddy.test_operational_recipients.OperationalRecipientTests.'
        'test_actual_smtp_postmaster_and_aliases_deliver_into_the_selected_target',
        'tests.maddy.test_operational_recipients.OperationalRecipientTests.'
        'test_complete_case_insensitive_bare_tokens_deliver_but_lookalikes_and_senders_fail',
        'tests.maddy.test_operational_recipients.OperationalRecipientTests.'
        'test_ordinary_recipient_delivery_and_esmtp_parameter_validation_are_preserved',
    ),
    'T18.01': (SNAPSHOT_CAPTURE_TEST,),
    'T19.01': (
        f'{ACME_COORDINATION_TESTS}.'
        'test_real_acquisition_allows_mailbox_cli_but_excludes_capture_until_completion',
    ),
    'T19.02': (
        SNAPSHOT_CAPTURE_TEST,
        f'{LOCK_EXCLUSION_TESTS}.'
        'test_real_contended_renewal_is_acquired_before_maddy_and_waits_are_bounded',
    ),
    'T19.03': (
        f'{LOCK_EXCLUSION_TESTS}.'
        'test_real_contended_renewal_is_acquired_before_maddy_and_waits_are_bounded',
        f'{LOCK_EXCLUSION_TESTS}.'
        'test_actual_stable_lock_files_reject_modes_symlinks_and_unexpected_owners',
    ),
    'T19.04': (
        f'{ACME_COORDINATION_TESTS}.'
        'test_sigkill_of_host_renewal_preserves_surviving_certbot_exclusion_until_reconciled',
        f'{ACME_COORDINATION_TESTS}.'
        'test_lost_attachment_releases_parent_locks_but_live_certbot_still_blocks_capture',
        'tests.maddy.test_surviving_helper.SurvivingHelperTests.'
        'test_sigkill_parent_keeps_actual_cli_survivor_and_blocks_backup_and_duplicate_writes',
    ),
    "T18.05": (
        "tests.maddy.test_restic_restore.NativeResticRestoreTests."
        "test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository",
    ),
    'T15.01': (
        f'{CERTBOT_TESTS}.test_real_issuance_and_ordinary_renewal_preserve_a_current_complete_pair',
        f'{CERTBOT_TESTS}.test_host_renewal_publishes_a_new_pair_only_when_certbot_decides_it_is_due',
        f'{CERTBOT_TESTS}.'
        'test_failed_real_http01_validation_preserves_the_previous_pair_and_generation',
        f'{TLS_CONSUMER_TESTS}.'
        'test_failed_consumers_are_independent_and_retry_without_new_issuance_serves_the_pair',
    ),
    'T15.02': (
        'tests.acme.test_certificate_validation.CertificateValidationTests.'
        'test_invalid_mismatched_wrong_host_expired_and_untrusted_complete_pairs_are_rejected',
    ),
    'T15.04': (
        f'{TLS_CONSUMER_TESTS}.'
        'test_failed_consumers_are_independent_and_retry_without_new_issuance_serves_the_pair',
    ),
    'T15.05': (
        f'{TLS_CONSUMER_TESTS}.'
        'test_absent_first_dashboard_is_allowed_but_a_missing_deployed_dashboard_stays_pending',
    ),
    "T18.06": (
        f"{RECOVERY_TESTS}.test_encrypted_restore_then_partial_body_repair_preserves_newer_mail",
        f"{RECOVERY_TESTS}.test_real_commit_failure_after_body_removal_keeps_a_durable_warning",
    ),
    "T21.01": (
        f"{API_TESTS}.test_streamed_body_without_length_is_bounded_before_routing",
        f"{API_TESTS}."
        "test_exact_body_limit_and_misleading_lengths_cannot_bypass_stream_counting",
    ),
    'T20.01': (
        'tests.runtime.test_bind_admission.BindAdmissionTests.'
        'test_actual_compose_refuses_every_missing_source_without_creating_replacements',
        'tests.runtime.test_bind_admission.BindAdmissionTests.'
        'test_actual_compose_wrong_source_types_cannot_start_a_healthy_application',
    ),
    'T20.02': (
        'tests.maddy.test_admission.MaddyAdmissionTests.'
        'test_actual_wrong_architecture_image_metadata_fails_both_host_admissions',
        'tests.maddy.test_admission.MaddyAdmissionTests.'
        'test_changed_mounts_and_missing_or_wrong_type_databases_fail_without_initialization',
        'tests.maddy.test_admission.MaddyAdmissionTests.'
        'test_real_other_image_identity_is_rejected_before_any_maddy_cli',
    ),
    'T20.03': (
        'tests.runtime.test_schema_startup.SchemaStartupTests.'
        'test_incompatible_schema_exits_without_accepting_writes_or_replacing_existing_state',
        'tests.runtime.test_schema_startup.SchemaStartupTests.'
        'test_interrupted_real_schema_commit_remains_closed_until_compatible_state_is_restored',
        'tests.runtime.test_helper_response_types.RuntimeHelperResponseTests.'
        'test_actual_https_runtime_rejects_non_integer_and_incompatible_wire_versions',
        'tests.runtime.test_helper_response_types.RuntimeHelperResponseTests.'
        'test_actual_https_runtime_rejects_non_boolean_success_flags_without_false_commits',
        'tests.integration.test_helper_version.HelperVersionApiTests.'
        'test_valid_boolean_denial_remains_a_safe_root_helper_error',
        f'{PROTOCOL_TESTS}.'
        'test_invalid_schema_address_and_password_requests_never_execute_real_cli',
    ),
    'T20.04': (
        f'{STORAGE_FAILURE_TESTS}.'
        'test_real_busy_sqlite_cannot_authorize_work_or_report_success_and_recovers',
        f'{STORAGE_FAILURE_TESTS}.'
        'test_real_corrupt_database_cannot_authorize_work_or_reinitialize_state',
        f'{STORAGE_FAILURE_TESTS}.'
        'test_native_sqlite_full_rolls_back_challenge_and_cannot_claim_a_committed_login',
        f'{RUNTIME_TESTS}.test_read_only_authentication_database_fails_without_bypass_and_recovers',
    ),
    'T20.05': (
        'tests.maddy.test_journal_storage.JournalStorageTests.'
        'test_native_read_only_journal_prevents_unrecorded_mutation_and_recovers',
        'tests.maddy.test_journal_storage.JournalStorageTests.'
        'test_native_full_journal_prevents_unrecorded_mutation_and_recovers',
        'tests.maddy.test_rollback_package.RealRollbackPackageTests.'
        'test_pinned_image_and_host_file_restore_preserves_real_mail_newer_than_the_package',
        'tests.maddy.test_restic_restore.NativeResticRestoreTests.'
        'test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository',
    ),
    'T21.02': (
        f'{NETWORK_DEADLINE_TESTS}.'
        'test_stalled_real_smtp_times_out_without_enabling_otp_or_blocking_health',
        f'{NETWORK_DEADLINE_TESTS}.'
        'test_stalled_udp_dns_and_full_queues_preserve_deadlines_and_health',
        f'{NETWORK_DEADLINE_TESTS}.test_stalled_unix_helper_fails_safely_without_blocking_health',
        f'{BOUNDARY_TESTS}.test_partial_request_times_out_without_work_and_the_worker_recovers',
        f'{BOUNDARY_TESTS}.test_four_real_idle_connections_fill_the_queue_and_excess_is_refused',
    ),
    "T21.03": (
        f"{API_TESTS}.test_cancelled_hash_retains_its_permit_until_real_hashing_finishes",
    ),
    'T22.01': (
        f'{HOUSEKEEPING_TESTS}.'
        'test_cleanup_preserves_active_shared_budgets_until_the_exact_window_boundary',
        f'{HOUSEKEEPING_TESTS}.'
        'test_cleanup_never_reopens_consumed_recovery_codes_or_the_totp_replay_watermark',
    ),
    'T22.02': (
        'tests.unit.test_history_retention.HistoryRetentionTests.'
        'test_audit_and_unreferenced_relay_history_expire_in_bounded_batches_after_ninety_days',
        'tests.runtime.test_log_retention.ContainerLogRetentionTests.'
        'test_shipped_local_driver_rotates_old_output_and_retains_current_logs',
    ),
    'T22.03': (
        f'{HOUSEKEEPING_TESTS}.'
        'test_current_global_and_per_mailbox_receipt_evidence_survives_old_history_cleanup',
        f'{HOUSEKEEPING_TESTS}.'
        'test_journal_retention_keeps_every_unresolved_operation_and_active_recent_completion',
    ),
    'T22.04': (
        'tests.integration.test_host_alert_delivery.HostAlertDeliveryTests.'
        'test_missed_backup_capacity_activation_and_expiry_alerts_retry_without_dashboard',
        'tests.integration.test_host_alert_delivery.HostAlertDeliveryTests.'
        'test_actual_free_inode_threshold_and_certificate_warning_windows_are_delivered',
        'tests.integration.test_host_alert_delivery.HostAlertDeliveryTests.'
        'test_cli_reports_failed_or_deferred_work_and_recovery_through_the_independent_outbox',
        'tests.integration.test_external_alert_delivery.ExternalAlertDeliveryTests.'
        'test_actual_expired_and_wrong_hostname_tls_do_not_block_independent_smtp_delivery',
    ),
    'T22.05': (
        'tests.integration.test_external_alert_delivery.ExternalAlertDeliveryTests.'
        'test_real_tls_endpoint_outage_and_recovery_are_delivered_retried_in_order_and_deduplicated',
        'tests.integration.test_external_alert_delivery.ExternalAlertDeliveryTests.'
        'test_silent_host_tls_times_out_and_still_delivers_independent_alerts',
    ),
}
