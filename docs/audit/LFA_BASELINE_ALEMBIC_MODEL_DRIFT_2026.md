# LFA Baseline Alembic Model Drift — 2026

## Scope and conclusion

This is a baseline maintenance finding, not a generated migration proposal.
No listed operation may be applied without a separate migration audit.

The comparison was run twice against fresh, disposable UTF-8 PostgreSQL
databases:

- exact PC5 baseline `ccc9efaa9026612f6f65046eb4f2d6538c30318d`, Alembic head
  `2026_09_13_2000`;
- PC6A branch after upgrade to Alembic head `2026_09_13_3000`.

Both comparisons produced exactly the same 301 autogenerate operations across
the same 67 tables. Therefore PC6A did not introduce the repository-level
model drift. There is no drift for the new `juggling_verifications` table or
the three PC6A review columns on `juggling_contact_events`.

The only reported `juggling_contact_events` items are three pre-existing index
metadata differences, identical on PC5 and PC6A:

- `ix_juggling_contact_events_annotation_review_status` — `remove_index`;
- `ix_juggling_contact_events_deleted_at` — `remove_index`;
- `ix_juggling_contact_events_training_filter` — `remove_index`.

## Exact operation inventory

| Operation | Count |
|---|---:|
| `modify_comment` | 150 |
| `remove_index` | 76 |
| `add_index` | 41 |
| `remove_fk` | 7 |
| `remove_column` | 6 |
| `remove_constraint` | 6 |
| `modify_type` | 6 |
| `add_fk` | 5 |
| `add_constraint` | 2 |
| `modify_nullable` | 1 |
| `remove_table` | 1 |
| **Total** | **301** |

The non-comment/non-index structural differences are:

- `al_import_logs`: model metadata would remove the table.
- `clubs`: remove `uq_clubs_code`.
- `credit_transactions`: remove `campaign_id`, `sponsor_id`, and their two
  foreign keys.
- `football_category_movement_events`: remove
  `uq_football_movement_idempotency_key`.
- `football_skill_assessments`: type differences on `validated_at`,
  `archived_at`, and `status_changed_at`.
- `game_configurations`: replace the `game_preset_id` foreign key.
- `juggling_consents`: remove `uq_juggling_consents_user_id`.
- `juggling_pose_snapshots`: add `ux_juggling_pose_snapshots_event`.
- `match_results`: replace the `user_id` and `match_structure_id` foreign
  keys.
- `match_structures`: replace the `session_id` foreign key.
- `pitch_instructor_assignments`: add `uq_pitch_master_per_semester`.
- `quiz_answer_options`: type difference on `option_type`.
- `semester_schedule_configs`: remove
  `semester_schedule_configs_semester_id_key`.
- `sessions`: replace the `pitch_id` foreign key.
- `skill_rewards`: remove `uq_skill_rewards_user_source_skill`.
- `sponsor_audience_entries`: type difference on `foot_dominance`.
- `sponsor_campaigns`: remove `unlock_cost`, `specialization_type`, and
  `credit_grant_amount`.
- `tournament_status_history`: change `changed_by` nullability.
- `user_face_embeddings`: remove `user_face_embeddings_user_id_key`.
- `user_licenses`: remove `academy_id_color`.
- `users`: type difference on `reviewed_at`.

The remaining differences are index naming/definition and database/ORM comment
differences. They span 67 tables, including adaptive learning, assessment,
booking, card, credit, football, juggling, match, session, sponsor, tournament,
user, virtual-training, and XP tables.

## Reproducibility and release impact

PC6A fresh upgrade, targeted downgrade to `2026_09_13_2000`, re-upgrade to
`2026_09_13_3000`, and post-upgrade verification tests pass. The immutable
verification trigger and all PC6A constraints are recreated successfully.

The generic `alembic check` command remains non-zero because it detects the
baseline inventory above. This does not prevent deterministic application of
the checked-in migration chain. It does mean repository-wide autogeneration is
unsafe until the model/migration metadata is reconciled in a separately scoped
maintenance workstream.

Status: **BASELINE MAINTENANCE FINDING — NOT CAUSED BY PC6A — NOT A PC6A RELEASE BLOCKER**
