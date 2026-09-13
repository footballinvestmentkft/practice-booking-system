import XCTest
@testable import LFAEducationCenter

final class PlayerIdentityContractTests: XCTestCase {
    func testPlayerLicenseDecodesCanonicalSeasonAssignment() throws {
        let json = """
        {
          "id": 41,
          "user_id": 7,
          "specialization_type": "LFA_FOOTBALL_PLAYER",
          "canonical_program_id": "LFA_FOOTBALL_PLAYER",
          "current_level": 1,
          "is_active": true,
          "onboarding_completed": false,
          "started_at": "2026-09-10T10:00:00Z",
          "expires_at": "2026-10-10T10:00:00Z",
          "season_start": "2026-07-01",
          "season_end": "2027-06-30",
          "season_base_category": "YOUTH",
          "effective_category": "AMATEUR",
          "base_participation_retained": true
        }
        """.data(using: .utf8)!

        let license = try JSONDecoder().decode(LFAPlayerLicense.self, from: json)

        XCTAssertEqual(license.canonicalProgramId, "LFA_FOOTBALL_PLAYER")
        XCTAssertEqual(license.seasonStart, "2026-07-01")
        XCTAssertEqual(license.seasonEnd, "2027-06-30")
        XCTAssertEqual(license.seasonBaseCategory, "YOUTH")
        XCTAssertEqual(license.effectiveCategory, "AMATEUR")
        XCTAssertEqual(license.baseParticipationRetained, true)
    }

    func testHistoricalLicenseWithoutAssignmentRemainsDecodable() throws {
        let json = """
        {
          "id": 42,
          "user_id": 8,
          "specialization_type": "LFA_FOOTBALL_PLAYER",
          "canonical_program_id": "LFA_FOOTBALL_PLAYER",
          "current_level": 1,
          "is_active": true,
          "onboarding_completed": true,
          "started_at": null,
          "expires_at": null,
          "season_start": null,
          "season_end": null,
          "season_base_category": null,
          "effective_category": null,
          "base_participation_retained": null
        }
        """.data(using: .utf8)!

        let license = try JSONDecoder().decode(LFAPlayerLicense.self, from: json)

        XCTAssertNil(license.seasonBaseCategory)
        XCTAssertNil(license.effectiveCategory)
    }

    func testPlayerSessionAvailabilityDecodesCanonicalLifecycleProjection() throws {
        let json = """
        {
          "session_id": 91,
          "title": "Player training",
          "date_start": "2026-10-02T18:00:00",
          "date_end": "2026-10-02T19:30:00",
          "category": "AMATEUR",
          "capacity": 12,
          "confirmed": 8,
          "available": 4,
          "waitlisted": 0,
          "booking_id": null,
          "participation_status": "available"
        }
        """.data(using: .utf8)!

        let session = try JSONDecoder().decode(PlayerSessionAvailabilityDTO.self, from: json)

        XCTAssertEqual(session.sessionId, 91)
        XCTAssertEqual(session.category, "AMATEUR")
        XCTAssertEqual(session.available, 4)
        XCTAssertEqual(session.participationStatus, "available")
        XCTAssertEqual(PlayerParticipationAPI.availabilityPath, "/api/v1/sessions/player/available")
        XCTAssertEqual(PlayerParticipationAPI.bookingPath, "/api/v1/bookings/")
    }

    func testRepeatedCancellationResponseRemainsDecodable() throws {
        let json = """
        {
          "message": "Booking cancelled successfully",
          "cancelled_booking_id": 44,
          "session_id": 91,
          "replayed": true
        }
        """.data(using: .utf8)!

        let result = try JSONDecoder().decode(PlayerBookingCancellationDTO.self, from: json)

        XCTAssertEqual(result.cancelledBookingId, 44)
        XCTAssertEqual(result.sessionId, 91)
        XCTAssertTrue(result.replayed)
        XCTAssertNil(result.promotedBookingId)
    }

    func testCanonicalPlayerEducationContractDecodesReleaseAndAssessment() throws {
        let json = """
        {
          "program_id": "LFA_FOOTBALL_PLAYER",
          "track_id": "track-1",
          "release_id": "release-1",
          "release_version": 3,
          "locale": "hu",
          "modules": [{
            "id": "module-1", "stable_key": "rules", "name": "Szabályok",
            "lessons": [{
              "id": "lesson-1", "stable_key": "laws-of-the-game",
              "title": "Laws of the Game", "topic_key": "football.rules.laws_of_the_game",
              "components": [],
              "assessments": [{
                "id": "assessment-1", "stable_key": "laws-medium",
                "delivery_mode": "ADAPTIVE", "purpose": "FORMATIVE",
                "difficulty": "MEDIUM", "variants": [{"quiz_id": 12, "locale": "en"}]
              }]
            }]
          }]
        }
        """.data(using: .utf8)!

        let curriculum = try JSONDecoder().decode(EducationCurriculum.self, from: json)

        XCTAssertEqual(curriculum.programId, "LFA_FOOTBALL_PLAYER")
        XCTAssertEqual(curriculum.releaseVersion, 3)
        XCTAssertEqual(curriculum.modules.first?.stableKey, "rules")
        XCTAssertEqual(curriculum.modules.first?.lessons.first?.assessments.first?.variants.first?.quizId, 12)
        XCTAssertEqual(PlayerEducationAPI.tracksPath, "/api/v1/education/programs/LFA_FOOTBALL_PLAYER/tracks")
    }

    func testCanonicalPlayerAssessmentContractPreservesReleaseAndIssuedSnapshotIdentity() throws {
        let json = """
        {
          "id": "attempt-1",
          "track_release_id": "release-3",
          "lesson_id": "lesson-7",
          "lesson_assessment_id": "assessment-2",
          "quiz_revision": 4,
          "locale": "en-GB",
          "delivery_mode": "ADAPTIVE",
          "status": "IN_PROGRESS",
          "questions": [{
            "id": "issued-9", "sequence": 1,
            "question_type": "MULTIPLE_CHOICE", "points": 2,
            "text": "Which option?",
            "options": [{"id": 31, "text": "Option A", "order": 1}]
          }]
        }
        """.data(using: .utf8)!

        let attempt = try JSONDecoder().decode(PlayerAssessmentAttempt.self, from: json)

        XCTAssertEqual(attempt.trackReleaseId, "release-3")
        XCTAssertEqual(attempt.lessonAssessmentId, "assessment-2")
        XCTAssertEqual(attempt.quizRevision, 4)
        XCTAssertEqual(attempt.questions.first?.id, "issued-9")
        XCTAssertEqual(attempt.questions.first?.options.first?.id, 31)
        XCTAssertEqual(
            PlayerAssessmentAPI.answerPath(attemptId: "attempt-1"),
            "/api/v1/education/assessment-attempts/attempt-1/answers"
        )
    }

    func testCanonicalPlayerAssessmentOutcomeDecodesImmutableProvenance() throws {
        let json = """
        {
          "attempt_id": "attempt-1", "status": "COMPLETED",
          "total_questions": 10, "answered_questions": 10,
          "correct_answers": 1, "total_points": 10, "earned_points": 1,
          "score_percent": 10.0, "passed": false, "xp_awarded": 0,
          "track_release_id": "release-3", "lesson_id": "lesson-7",
          "lesson_assessment_id": "assessment-2", "quiz_revision": 4,
          "locale": "en-GB",
          "provenance": {"issued_question_snapshot_hash": "abc123"}
        }
        """.data(using: .utf8)!

        let outcome = try JSONDecoder().decode(PlayerAssessmentOutcome.self, from: json)

        XCTAssertEqual(outcome.totalQuestions, 10)
        XCTAssertEqual(outcome.answeredQuestions, 10)
        XCTAssertEqual(outcome.scorePercent, 10.0)
        XCTAssertEqual(outcome.xpAwarded, 0)
        XCTAssertEqual(outcome.quizRevision, 4)
        XCTAssertEqual(
            outcome.provenance["issued_question_snapshot_hash"],
            .string("abc123")
        )
    }
}
