"""Preparation Educational Quality V1 — cross-artifact coherence baseline (TOS-DEV04-I05).

Provider-independent, deterministic, network-free hard validation over final
PreparationArtifactPayloadsV1. Reuses EducationalQualityResult primitives and
inherits DEV03 worksheet EQ baseline checks.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ValidationError

from aieos.domains.education.application.models import PreparationArtifactPayloadsV1
from aieos.domains.education.content_payloads_v1 import (
    AnswerKeyV1,
    HomeworkV1,
    LessonPlanV1,
    QuizV1,
    TeacherNotesV1,
)
from aieos.domains.education.preparation_kit_v1 import AnswerKeySourceArtifactKind
from aieos.domains.education.worksheet_v1 import LearningObjectiveV1, WorksheetQuestionV1, WorksheetV1
from aieos.platform.education.quality_baseline import (
    EducationalQualityCheck,
    EducationalQualityResult,
    EducationalQualityStatus,
    evaluate_educational_quality_baseline_v1,
    find_unsupported_alignment_claim,
)

_SCHEMA_SKIP = "skipped: final artifact schema invalid"

_PREP_CHECK_CODES_AFTER_SCHEMA = (
    "shared_objectives_present",
    "lesson_plan_objectives_mapped",
    "worksheet_objectives_mapped",
    "quiz_objectives_mapped",
    "homework_objectives_mapped",
    "question_identifier_integrity",
    "answer_key_complete",
    "answer_key_reference_integrity",
    "cross_artifact_objective_consistency",
    "cross_artifact_topic_consistency",
    "unsupported_alignment_claim_absent",
    "teacher_notes_present",
)

_WORKSHEET_EQ_CODES = (
    "schema_valid",
    "learning_objectives_present",
    "question_count_valid",
    "objective_mapping_complete",
    "answer_key_complete",
    "bloom_labels_valid",
    "bloom_distribution_baseline",
    "difficulty_distribution_baseline",
    "unsupported_alignment_claim_absent",
)


def _check(code: str, passed: bool, explanation: str) -> EducationalQualityCheck:
    return EducationalQualityCheck(code=code, passed=passed, explanation=explanation)


def _objective_tuple(
    objectives: Iterable[LearningObjectiveV1],
) -> tuple[tuple[str, str], ...]:
    return tuple((obj.id, obj.text) for obj in objectives)


def _semantic_nonblank(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _revalidate(model: BaseModel, model_type: type[BaseModel]) -> None:
    model_type.model_validate(model.model_dump(mode="python"))


def _questions_for_kind(
    artifacts: PreparationArtifactPayloadsV1,
    kind: AnswerKeySourceArtifactKind,
) -> list[WorksheetQuestionV1]:
    if kind is AnswerKeySourceArtifactKind.WORKSHEET:
        return list(artifacts.worksheet.questions)
    if kind is AnswerKeySourceArtifactKind.QUIZ:
        return list(artifacts.quiz.questions)
    if kind is AnswerKeySourceArtifactKind.HOMEWORK:
        return list(artifacts.homework.questions)
    raise ValueError(f"unsupported answer-key source kind: {kind}")


def _mapping_ok(
    questions: Iterable[WorksheetQuestionV1],
    known: set[str],
) -> bool:
    return all(
        question.objective_ids
        and all(oid in known for oid in question.objective_ids)
        for question in questions
    )


def _collect_kit_text(artifacts: PreparationArtifactPayloadsV1) -> str:
    parts: list[str] = []

    lp = artifacts.lesson_plan
    parts.extend([lp.title, lp.opening, lp.closure, lp.formative_check])
    parts.extend(lp.materials)
    for objective in lp.learning_objectives:
        parts.append(objective.text)
    for section in lp.sections:
        parts.extend(
            [section.title, section.teacher_actions, section.learner_actions]
        )

    ws = artifacts.worksheet
    parts.extend([ws.title, ws.teacher_summary, ws.instructions])
    if ws.teacher_notes:
        parts.append(ws.teacher_notes)
    for objective in ws.learning_objectives:
        parts.append(objective.text)
    for question in ws.questions:
        parts.extend(
            [question.prompt, question.answer, question.explanation, *question.options]
        )
        if question.visual_description:
            parts.append(question.visual_description)

    for component in (artifacts.quiz, artifacts.homework):
        parts.extend([component.title, component.instructions])
        for objective in component.learning_objectives:
            parts.append(objective.text)
        for question in component.questions:
            parts.extend(
                [
                    question.prompt,
                    question.answer,
                    question.explanation,
                    *question.options,
                ]
            )
            if question.visual_description:
                parts.append(question.visual_description)

    ak = artifacts.answer_key
    parts.append(ak.title)
    for entry in ak.entries:
        parts.extend([entry.answer, entry.explanation])

    tn = artifacts.teacher_notes
    parts.append(tn.title)
    parts.extend(tn.notes)

    return "\n".join(parts)


def _prefixed_worksheet_checks(
    worksheet_result: EducationalQualityResult,
) -> list[EducationalQualityCheck]:
    return [
        _check(
            f"worksheet_{check.code}",
            check.passed,
            check.explanation,
        )
        for check in worksheet_result.checks
    ]


def _skipped_worksheet_checks() -> list[EducationalQualityCheck]:
    return [
        _check(f"worksheet_{code}", False, _SCHEMA_SKIP)
        for code in _WORKSHEET_EQ_CODES
    ]


def evaluate_preparation_educational_quality_v1(
    artifacts: PreparationArtifactPayloadsV1,
) -> EducationalQualityResult:
    """Hard preparation EQ + cross-artifact coherence over final typed payloads."""

    checks: list[EducationalQualityCheck] = []

    # 1. schema_valid — defense-in-depth revalidation of all six artifacts
    schema_failures: list[str] = []
    for label, model, model_type in (
        ("lesson_plan", artifacts.lesson_plan, LessonPlanV1),
        ("worksheet", artifacts.worksheet, WorksheetV1),
        ("quiz", artifacts.quiz, QuizV1),
        ("homework", artifacts.homework, HomeworkV1),
        ("answer_key", artifacts.answer_key, AnswerKeyV1),
        ("teacher_notes", artifacts.teacher_notes, TeacherNotesV1),
    ):
        try:
            _revalidate(model, model_type)
        except (ValidationError, TypeError, ValueError):
            schema_failures.append(label)

    schema_ok = not schema_failures
    checks.append(
        _check(
            "schema_valid",
            schema_ok,
            "all six final artifacts revalidate against authoritative contracts"
            if schema_ok
            else f"final artifact schema invalid: {', '.join(schema_failures)}",
        )
    )

    if not schema_ok:
        for code in _PREP_CHECK_CODES_AFTER_SCHEMA:
            checks.append(_check(code, False, _SCHEMA_SKIP))
        checks.extend(_skipped_worksheet_checks())
        return EducationalQualityResult(
            status=EducationalQualityStatus.FAIL,
            checks=tuple(checks),
        )

    # 2. shared_objectives_present
    learning_artifacts = (
        ("lesson_plan", artifacts.lesson_plan.learning_objectives),
        ("worksheet", artifacts.worksheet.learning_objectives),
        ("quiz", artifacts.quiz.learning_objectives),
        ("homework", artifacts.homework.learning_objectives),
    )
    shared_ok = True
    for _label, objectives in learning_artifacts:
        if not objectives:
            shared_ok = False
            break
        if any(
            not _semantic_nonblank(obj.id) or not _semantic_nonblank(obj.text)
            for obj in objectives
        ):
            shared_ok = False
            break
    checks.append(
        _check(
            "shared_objectives_present",
            shared_ok,
            "all four learning artifacts carry non-empty semantic learning objectives"
            if shared_ok
            else "one or more learning artifacts lack valid learning objectives",
        )
    )

    # 3. lesson_plan_objectives_mapped
    lp = artifacts.lesson_plan
    lp_known = {obj.id for obj in lp.learning_objectives}
    lp_map_ok = (
        bool(lp.objective_ids)
        and all(oid in lp_known for oid in lp.objective_ids)
        and all(
            section.objective_ids
            and all(oid in lp_known for oid in section.objective_ids)
            for section in lp.sections
        )
    )
    checks.append(
        _check(
            "lesson_plan_objectives_mapped",
            lp_map_ok,
            "lesson-plan and section objective references resolve"
            if lp_map_ok
            else "lesson-plan has unresolved or empty objective references",
        )
    )

    # 4–6. component objective mappings
    ws_known = {obj.id for obj in artifacts.worksheet.learning_objectives}
    ws_map_ok = _mapping_ok(artifacts.worksheet.questions, ws_known)
    checks.append(
        _check(
            "worksheet_objectives_mapped",
            ws_map_ok,
            "every worksheet question maps to valid worksheet objectives"
            if ws_map_ok
            else "worksheet has unresolved or empty objective references",
        )
    )

    quiz_known = {obj.id for obj in artifacts.quiz.learning_objectives}
    quiz_map_ok = _mapping_ok(artifacts.quiz.questions, quiz_known)
    checks.append(
        _check(
            "quiz_objectives_mapped",
            quiz_map_ok,
            "every quiz question maps to valid quiz objectives"
            if quiz_map_ok
            else "quiz has unresolved or empty objective references",
        )
    )

    hw_known = {obj.id for obj in artifacts.homework.learning_objectives}
    hw_map_ok = _mapping_ok(artifacts.homework.questions, hw_known)
    checks.append(
        _check(
            "homework_objectives_mapped",
            hw_map_ok,
            "every homework question maps to valid homework objectives"
            if hw_map_ok
            else "homework has unresolved or empty objective references",
        )
    )

    # 7. question_identifier_integrity
    id_ok = True
    id_explain = "question IDs unique within each component; answer-key composites unique"
    for label, questions in (
        ("worksheet", artifacts.worksheet.questions),
        ("quiz", artifacts.quiz.questions),
        ("homework", artifacts.homework.questions),
    ):
        ids = [q.id for q in questions]
        if any(not _semantic_nonblank(qid) for qid in ids):
            id_ok = False
            id_explain = f"{label} has blank question ID"
            break
        if len(ids) != len(set(ids)):
            id_ok = False
            id_explain = f"{label} has duplicate question IDs"
            break
    if id_ok:
        ak_keys = [
            (entry.source_artifact_kind, entry.source_question_id)
            for entry in artifacts.answer_key.entries
        ]
        if len(ak_keys) != len(set(ak_keys)):
            id_ok = False
            id_explain = "answer key has duplicate composite source references"
    checks.append(_check("question_identifier_integrity", id_ok, id_explain))

    # 8. answer_key_complete
    expected_keys: list[tuple[AnswerKeySourceArtifactKind, str]] = []
    for kind, questions in (
        (AnswerKeySourceArtifactKind.WORKSHEET, artifacts.worksheet.questions),
        (AnswerKeySourceArtifactKind.QUIZ, artifacts.quiz.questions),
        (AnswerKeySourceArtifactKind.HOMEWORK, artifacts.homework.questions),
    ):
        for question in questions:
            expected_keys.append((kind, question.id))
    actual_keys = [
        (entry.source_artifact_kind, entry.source_question_id)
        for entry in artifacts.answer_key.entries
    ]
    expected_set = set(expected_keys)
    actual_set = set(actual_keys)
    ak_complete = (
        expected_set == actual_set
        and len(actual_keys) == len(expected_keys)
        and len(artifacts.answer_key.entries)
        == (
            len(artifacts.worksheet.questions)
            + len(artifacts.quiz.questions)
            + len(artifacts.homework.questions)
        )
    )
    checks.append(
        _check(
            "answer_key_complete",
            ak_complete,
            (
                f"answer key contains {len(actual_set)}/{len(expected_set)} "
                "required source-question bindings"
            ),
        )
    )

    # 9. answer_key_reference_integrity
    ref_ok = True
    ref_explain = "every answer-key entry answer and explanation matches its source question"
    question_index: dict[tuple[AnswerKeySourceArtifactKind, str], WorksheetQuestionV1] = {}
    for kind in (
        AnswerKeySourceArtifactKind.WORKSHEET,
        AnswerKeySourceArtifactKind.QUIZ,
        AnswerKeySourceArtifactKind.HOMEWORK,
    ):
        for question in _questions_for_kind(artifacts, kind):
            question_index[(kind, question.id)] = question
    for entry in artifacts.answer_key.entries:
        source = question_index.get((entry.source_artifact_kind, entry.source_question_id))
        if source is None:
            ref_ok = False
            ref_explain = "answer key references a missing source question"
            break
        if entry.answer != source.answer or entry.explanation != source.explanation:
            ref_ok = False
            ref_explain = (
                "answer key answer/explanation differs from source question "
                f"{entry.source_artifact_kind.value}/{entry.source_question_id}"
            )
            break
    checks.append(_check("answer_key_reference_integrity", ref_ok, ref_explain))

    # 10. cross_artifact_objective_consistency (ordered exact tuples)
    obj_sequences = [
        _objective_tuple(artifacts.lesson_plan.learning_objectives),
        _objective_tuple(artifacts.worksheet.learning_objectives),
        _objective_tuple(artifacts.quiz.learning_objectives),
        _objective_tuple(artifacts.homework.learning_objectives),
    ]
    obj_consistent = all(seq == obj_sequences[0] for seq in obj_sequences[1:])
    obj_count = len(obj_sequences[0])
    checks.append(
        _check(
            "cross_artifact_objective_consistency",
            obj_consistent,
            (
                f"all four learning artifacts carry the same {obj_count} "
                "canonical objectives"
                if obj_consistent
                else "quiz or other component objective values differ from "
                "canonical objective lineage"
            ),
        )
    )

    # 11. cross_artifact_topic_consistency — V1 structural lineage baseline
    topic_ok = (
        obj_consistent
        and lp_map_ok
        and ws_map_ok
        and quiz_map_ok
        and hw_map_ok
    )
    checks.append(
        _check(
            "cross_artifact_topic_consistency",
            topic_ok,
            (
                "DEV04 V1 topic coherence baseline satisfied via canonical "
                "objective lineage and valid component objective mappings"
                if topic_ok
                else "DEV04 V1 topic coherence baseline failed: canonical "
                "objective lineage or component objective mappings invalid"
            ),
        )
    )

    # 12. unsupported_alignment_claim_absent (whole kit)
    claim = find_unsupported_alignment_claim(_collect_kit_text(artifacts))
    alignment_ok = claim is None
    checks.append(
        _check(
            "unsupported_alignment_claim_absent",
            alignment_ok,
            "no unsupported curriculum/board alignment claims detected across kit"
            if alignment_ok
            else f"unsupported alignment claim detected near {claim!r}",
        )
    )

    # 13. teacher_notes_present
    tn = artifacts.teacher_notes
    notes_ok = (
        _semantic_nonblank(tn.title)
        and len(tn.notes) >= 1
        and all(_semantic_nonblank(note) for note in tn.notes)
    )
    checks.append(
        _check(
            "teacher_notes_present",
            notes_ok,
            "teacher notes title and at least one semantic note present"
            if notes_ok
            else "teacher notes missing, blank, or empty",
        )
    )

    # 14+. inherited DEV03 worksheet EQ (prefixed codes, existing order)
    worksheet_result = evaluate_educational_quality_baseline_v1(artifacts.worksheet)
    checks.extend(_prefixed_worksheet_checks(worksheet_result))

    status = (
        EducationalQualityStatus.PASS
        if all(check.passed for check in checks)
        else EducationalQualityStatus.FAIL
    )
    return EducationalQualityResult(status=status, checks=tuple(checks))
