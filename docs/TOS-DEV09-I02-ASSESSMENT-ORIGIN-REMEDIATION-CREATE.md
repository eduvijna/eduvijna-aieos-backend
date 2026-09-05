# TOS-DEV09-I02 Assessment-origin remediation create

`POST /api/v1/teaching/works/from-classroom-assessment` creates one
`remediate_class` TeachingWork and its immutable
TeachingWorkRemediationOrigin from locked ClassroomAssessment evidence.

The request accepts only `assessment_id`,
`expected_assessment_aggregate_revision`, `goal_text`, `target_date`, `locale`,
and optional `subject`/`topic`. Tenant, teacher, ClassRef, class label, Content
identity, result level, and composition identities are authority-derived.

Every attempt, including an idempotent replay, requires both
`teaching.work.create` and `assessment.classroom.read`. New creation requires a
RECORDED exact-revision Assessment owned by the represented teacher, a current
assignable ClassRef, and coherent tenant-visible Teaching composition. The
School Context display label becomes the Work class label.

Work, origin, `teaching.work.remediation.create` security audit, and
idempotency outcome commit atomically on the Teaching UoW connection. No
outbox, NATS, Temporal Improve event, Assessment note, or Teaching observation
is copied.

Migration `tosd090002` widens only the three security audit CHECK constraints.
Downgrade restores the complete `tosd090001` vocabulary and refuses when
remediation audit evidence exists.
