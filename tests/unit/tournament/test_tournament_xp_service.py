"""
Comprehensive unit tests for tournament_xp_service.py

Tests all 5 functions with happy path, edge cases, error handling, and validation.
Covers: create_tournament_rewards, get_tournament_rewards, distribute_rewards,
        calculate_tournament_xp, award_manual_reward
"""
import pytest
import uuid
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session
from decimal import Decimal

from app.services.tournament import tournament_xp_service
from app.models import (
    TournamentReward,
    TournamentRanking,
    User,
    CreditTransaction,
    TransactionType
)
from app.models.user import UserRole
from app.models.semester import Semester, SemesterStatus
from app.models.specialization import SpecializationType


def create_test_user(db: Session, email: str, name: str, role: UserRole = UserRole.STUDENT) -> User:
    """Helper function to create a test user with unique email"""
    # Add UUID suffix to prevent duplicate key violations
    unique_email = f"{email.split('@')[0]}+{uuid.uuid4().hex[:8]}@{email.split('@')[1]}"
    user = User(
        email=unique_email,
        name=name,
        password_hash="test_hash_123",
        role=role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_tournament(db: Session, tournament_id: int = None) -> Semester:
    """Helper function to create a test tournament with unique code and instructor"""
    from datetime import date, timedelta

    # Create instructor (required for distribute_rewards)
    instructor = User(
        email=f"instructor+{uuid.uuid4().hex[:8]}@test.com",
        name="Test Instructor",
        password_hash="test_hash",
        role=UserRole.INSTRUCTOR
    )
    db.add(instructor)
    db.flush()

    # Add UUID suffix to prevent duplicate key violations (max 20 chars for code)
    uuid_suffix = uuid.uuid4().hex[:4]
    base_code = f"TOURN-TEST-{tournament_id or 1}"
    max_prefix = 20 - 1 - len(uuid_suffix)  # Max 20 chars DB constraint
    unique_code = f"{base_code[:max_prefix]}-{uuid_suffix}"

    tournament = Semester(
        id=tournament_id,
        code=unique_code,
        name="Test Tournament",
        start_date=date.today() + timedelta(days=7),
        end_date=date.today() + timedelta(days=7),
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        master_instructor_id=instructor.id,  # Assign instructor
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
        age_group="YOUTH"
    )
    db.add(tournament)
    db.commit()
    db.refresh(tournament)
    return tournament


# ============================================================================
# TEST create_tournament_rewards()
# ============================================================================

class TestCreateTournamentRewards:
    """Test create_tournament_rewards() function"""

    def test_create_rewards_success(self, test_db: Session):
        """Happy path: Create rewards with all positions"""
        tournament = create_test_tournament(test_db)

        rewards_config = {
            "1ST": {"xp": 500, "credits": 100},
            "2ND": {"xp": 300, "credits": 50},
            "3RD": {"xp": 200, "credits": 25},
            "PARTICIPANT": {"xp": 50, "credits": 0}
        }

        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        # Verify rewards were created
        rewards = test_db.query(TournamentReward).filter(
            TournamentReward.tournament_id == tournament.id
        ).all()

        assert len(rewards) == 4

        # Check 1st place
        first_place = next(r for r in rewards if r.position == "1ST")
        assert first_place.xp_amount == 500
        assert first_place.credits_reward == 100

        # Check 2nd place
        second_place = next(r for r in rewards if r.position == "2ND")
        assert second_place.xp_amount == 300
        assert second_place.credits_reward == 50

        # Check 3rd place
        third_place = next(r for r in rewards if r.position == "3RD")
        assert third_place.xp_amount == 200
        assert third_place.credits_reward == 25

        # Check participant
        participant = next(r for r in rewards if r.position == "PARTICIPANT")
        assert participant.xp_amount == 50
        assert participant.credits_reward == 0

    def test_create_rewards_partial_config(self, test_db: Session):
        """Create rewards with partial configuration (only 1st and 2nd)"""
        tournament = create_test_tournament(test_db)

        rewards_config = {
            "1ST": {"xp": 1000, "credits": 200},
            "2ND": {"xp": 500, "credits": 100}
        }

        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        rewards = test_db.query(TournamentReward).filter(
            TournamentReward.tournament_id == tournament.id
        ).all()

        assert len(rewards) == 2

    def test_create_rewards_missing_xp(self, test_db: Session):
        """Create rewards with missing XP value (defaults to 0)"""
        tournament = create_test_tournament(test_db)

        rewards_config = {
            "1ST": {"credits": 100}  # Missing 'xp'
        }

        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        reward = test_db.query(TournamentReward).filter(
            TournamentReward.tournament_id == tournament.id
        ).first()

        assert reward.xp_amount == 0
        assert reward.credits_reward == 100

    def test_create_rewards_missing_credits(self, test_db: Session):
        """Create rewards with missing credits value (defaults to 0)"""
        tournament = create_test_tournament(test_db)

        rewards_config = {
            "1ST": {"xp": 500}  # Missing 'credits'
        }

        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        reward = test_db.query(TournamentReward).filter(
            TournamentReward.tournament_id == tournament.id
        ).first()

        assert reward.xp_amount == 500
        assert reward.credits_reward == 0

    def test_create_rewards_empty_config(self, test_db: Session):
        """Create rewards with empty configuration"""
        tournament = create_test_tournament(test_db)

        rewards_config = {}

        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        rewards = test_db.query(TournamentReward).filter(
            TournamentReward.tournament_id == tournament.id
        ).all()

        assert len(rewards) == 0


# ============================================================================
# TEST get_tournament_rewards()
# ============================================================================

class TestGetTournamentRewards:
    """Test get_tournament_rewards() function"""

    def test_get_rewards_success(self, test_db: Session):
        """Happy path: Get all rewards for a tournament"""
        tournament = create_test_tournament(test_db)

        # Create rewards
        rewards_config = {
            "1ST": {"xp": 500, "credits": 100},
            "2ND": {"xp": 300, "credits": 50},
            "3RD": {"xp": 200, "credits": 25}
        }
        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        # Get rewards
        rewards = tournament_xp_service.get_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        assert isinstance(rewards, dict)
        assert len(rewards) == 3
        assert "1ST" in rewards
        assert "2ND" in rewards
        assert "3RD" in rewards

        assert rewards["1ST"].xp_amount == 500
        assert rewards["2ND"].xp_amount == 300
        assert rewards["3RD"].xp_amount == 200

    def test_get_rewards_empty_tournament(self, test_db: Session):
        """Get rewards for tournament with no rewards configured"""
        tournament = create_test_tournament(test_db)

        rewards = tournament_xp_service.get_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        assert isinstance(rewards, dict)
        assert len(rewards) == 0

    def test_get_rewards_nonexistent_tournament(self, test_db: Session):
        """Get rewards for non-existent tournament"""
        rewards = tournament_xp_service.get_tournament_rewards(
            db=test_db,
            tournament_id=99999
        )

        assert isinstance(rewards, dict)
        assert len(rewards) == 0


# ============================================================================
# TEST distribute_rewards()
# ============================================================================

class TestDistributeRewards:
    """Test distribute_rewards() function"""

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_distribute_rewards_with_existing_config(self, mock_award_xp, test_db: Session):
        """Happy path: Distribute rewards with existing configuration"""
        tournament = create_test_tournament(test_db)

        # Create rewards config
        rewards_config = {
            "1ST": {"xp": 500, "credits": 100},
            "2ND": {"xp": 300, "credits": 50},
            "3RD": {"xp": 200, "credits": 25},
            "PARTICIPANT": {"xp": 50, "credits": 0}
        }
        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        # Create users
        user1 = create_test_user(test_db, "first@test.com", "First Place")
        user2 = create_test_user(test_db, "second@test.com", "Second Place")
        user3 = create_test_user(test_db, "third@test.com", "Third Place")
        user4 = create_test_user(test_db, "fourth@test.com", "Participant")

        # Create rankings
        rankings = [
            TournamentRanking(tournament_id=tournament.id, user_id=user1.id,
                            participant_type="INDIVIDUAL", rank=1, points=Decimal("100")),
            TournamentRanking(tournament_id=tournament.id, user_id=user2.id,
                            participant_type="INDIVIDUAL", rank=2, points=Decimal("90")),
            TournamentRanking(tournament_id=tournament.id, user_id=user3.id,
                            participant_type="INDIVIDUAL", rank=3, points=Decimal("80")),
            TournamentRanking(tournament_id=tournament.id, user_id=user4.id,
                            participant_type="INDIVIDUAL", rank=4, points=Decimal("70"))
        ]
        for ranking in rankings:
            test_db.add(ranking)
        test_db.commit()

        # Mock award_xp to return a mock UserStats
        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        # Distribute rewards
        stats = tournament_xp_service.distribute_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        # Verify stats
        assert stats['total_participants'] == 4
        assert stats['xp_distributed'] == 1050  # 500 + 300 + 200 + 50
        assert stats['credits_distributed'] == 175  # 100 + 50 + 25 + 0

        # Verify XP was awarded (4 times)
        assert mock_award_xp.call_count == 4

        # Verify credit transactions (scoped to users created in this test)
        test_user_ids = [user1.id, user2.id, user3.id, user4.id]
        transactions = test_db.query(CreditTransaction).filter(
            CreditTransaction.user_id.in_(test_user_ids)
        ).all()
        assert len(transactions) == 3  # Only 1st, 2nd, 3rd get credits

        # Verify user credits
        test_db.refresh(user1)
        test_db.refresh(user2)
        test_db.refresh(user3)
        test_db.refresh(user4)

        assert user1.credit_balance == 100
        assert user2.credit_balance == 50
        assert user3.credit_balance == 25
        assert user4.credit_balance is None or user4.credit_balance == 0

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_distribute_rewards_creates_default_config(self, mock_award_xp, test_db: Session):
        """Distribute rewards creates default config when none exists"""
        tournament = create_test_tournament(test_db)

        # Create a user and ranking
        user1 = create_test_user(test_db, "winner@test.com", "Winner")
        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user1.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=Decimal("100")
        )
        test_db.add(ranking)
        test_db.commit()

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        # Distribute rewards (should create default config)
        stats = tournament_xp_service.distribute_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        # Verify default rewards were created
        rewards = test_db.query(TournamentReward).filter(
            TournamentReward.tournament_id == tournament.id
        ).all()
        assert len(rewards) == 4

        # Verify stats
        assert stats['total_participants'] == 1
        assert stats['xp_distributed'] == 500  # Default 1st place
        assert stats['credits_distributed'] == 100

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_distribute_rewards_no_rankings(self, mock_award_xp, test_db: Session):
        """Distribute rewards with no rankings"""
        tournament = create_test_tournament(test_db)

        stats = tournament_xp_service.distribute_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        assert stats['total_participants'] == 0
        assert stats['xp_distributed'] == 0
        assert stats['credits_distributed'] == 0
        assert mock_award_xp.call_count == 0

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_distribute_rewards_team_ranking(self, mock_award_xp, test_db: Session, team_factory):
        """Distribute rewards with team ranking (no user_id)"""
        tournament = create_test_tournament(test_db)

        # Create test team dynamically
        team = team_factory(name="Test Team Alpha")

        # Create rewards
        rewards_config = {
            "1ST": {"xp": 500, "credits": 100}
        }
        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        # Create team ranking (no user_id)
        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=None,  # Team ranking
            team_id=team.id,
            participant_type="TEAM",
            rank=1,
            points=Decimal("100")
        )
        test_db.add(ranking)
        test_db.commit()

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        # Distribute rewards
        stats = tournament_xp_service.distribute_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        # Should not award XP/credits to teams
        assert stats['total_participants'] == 1
        assert stats['xp_distributed'] == 0
        assert stats['credits_distributed'] == 0
        assert mock_award_xp.call_count == 0

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_distribute_rewards_multiple_positions(self, mock_award_xp, test_db: Session):
        """Test all position mappings (1st, 2nd, 3rd, participant)"""
        tournament = create_test_tournament(test_db)

        # Create rewards
        rewards_config = {
            "1ST": {"xp": 500, "credits": 100},
            "2ND": {"xp": 300, "credits": 50},
            "3RD": {"xp": 200, "credits": 25},
            "PARTICIPANT": {"xp": 50, "credits": 10}
        }
        tournament_xp_service.create_tournament_rewards(
            db=test_db,
            tournament_id=tournament.id,
            rewards_config=rewards_config
        )

        # Create users for each position
        users = []
        for i in range(5):
            user = create_test_user(test_db, f"user{i}@test.com", f"User {i}")
            users.append(user)

            ranking = TournamentRanking(
                tournament_id=tournament.id,
                user_id=user.id,
                participant_type="INDIVIDUAL",
                rank=i + 1,
                points=Decimal(str(100 - i * 10))
            )
            test_db.add(ranking)
        test_db.commit()

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        # Distribute rewards
        stats = tournament_xp_service.distribute_rewards(
            db=test_db,
            tournament_id=tournament.id
        )

        # Verify
        assert stats['total_participants'] == 5
        # 500 (1st) + 300 (2nd) + 200 (3rd) + 50 (4th) + 50 (5th)
        assert stats['xp_distributed'] == 1100
        # 100 + 50 + 25 + 10 + 10
        assert stats['credits_distributed'] == 195


# ============================================================================
# TEST calculate_tournament_xp()
# ============================================================================

class TestCalculateTournamentXP:
    """Test calculate_tournament_xp() function"""

    def test_first_place(self):
        """1st place: base_xp * 5"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=1)
        assert xp == 500  # 100 * 5

    def test_first_place_custom_base(self):
        """1st place with custom base XP"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=1, base_xp=200)
        assert xp == 1000  # 200 * 5

    def test_second_place(self):
        """2nd place: base_xp * 3"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=2)
        assert xp == 300  # 100 * 3

    def test_second_place_custom_base(self):
        """2nd place with custom base XP"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=2, base_xp=150)
        assert xp == 450  # 150 * 3

    def test_third_place(self):
        """3rd place: base_xp * 2"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=3)
        assert xp == 200  # 100 * 2

    def test_third_place_custom_base(self):
        """3rd place with custom base XP"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=3, base_xp=250)
        assert xp == 500  # 250 * 2

    def test_fourth_to_tenth_place(self):
        """4th-10th place: base_xp * 1"""
        for rank in range(4, 11):
            xp = tournament_xp_service.calculate_tournament_xp(rank=rank)
            assert xp == 100, f"Rank {rank} should get base_xp"

    def test_fourth_to_tenth_custom_base(self):
        """4th-10th place with custom base XP"""
        for rank in range(4, 11):
            xp = tournament_xp_service.calculate_tournament_xp(rank=rank, base_xp=80)
            assert xp == 80, f"Rank {rank} should get custom base_xp"

    def test_eleventh_and_above(self):
        """11th+ place: participation_xp"""
        for rank in [11, 12, 15, 20, 50, 100]:
            xp = tournament_xp_service.calculate_tournament_xp(rank=rank)
            assert xp == 50, f"Rank {rank} should get participation_xp"

    def test_eleventh_custom_participation(self):
        """11th+ place with custom participation XP"""
        for rank in [11, 12, 20, 100]:
            xp = tournament_xp_service.calculate_tournament_xp(
                rank=rank,
                participation_xp=25
            )
            assert xp == 25, f"Rank {rank} should get custom participation_xp"

    def test_edge_case_rank_10(self):
        """Edge case: Rank 10 should get base_xp, not participation"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=10)
        assert xp == 100

    def test_edge_case_rank_11(self):
        """Edge case: Rank 11 should get participation_xp"""
        xp = tournament_xp_service.calculate_tournament_xp(rank=11)
        assert xp == 50

    def test_all_custom_parameters(self):
        """Test with all custom parameters"""
        xp = tournament_xp_service.calculate_tournament_xp(
            rank=1,
            base_xp=200,
            participation_xp=75
        )
        assert xp == 1000  # 200 * 5


# ============================================================================
# TEST award_manual_reward()
# ============================================================================

class TestAwardManualReward:
    """Test award_manual_reward() function"""

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_success(self, mock_award_xp, test_db: Session):
        """Happy path: Award both XP and credits"""
        tournament = create_test_tournament(test_db)
        user = create_test_user(test_db, "player@test.com", "Player")

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            xp_amount=250,
            credits_amount=50,
            reason="Great sportsmanship"
        )

        assert result is True

        # Verify XP was awarded with correct kwargs (user_id/xp_amount, not user/amount)
        mock_award_xp.assert_called_once_with(
            db=test_db,
            user_id=user.id,
            xp_amount=250,
            reason="Great sportsmanship"
        )

        # Verify credits were added
        test_db.refresh(user)
        assert user.credit_balance == 50

        # Verify transaction was created
        transaction = test_db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user.id
        ).first()
        assert transaction is not None
        assert transaction.amount == 50
        assert transaction.transaction_type == TransactionType.MANUAL_ADJUSTMENT.value
        assert transaction.description == "Great sportsmanship"

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_xp_only(self, mock_award_xp, test_db: Session):
        """Award only XP, no credits"""
        tournament = create_test_tournament(test_db)
        user = create_test_user(test_db, "player@test.com", "Player")

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            xp_amount=100,
            credits_amount=0,
            reason="Participation bonus"
        )

        assert result is True
        mock_award_xp.assert_called_once()

        # No credit transaction should be created
        transactions = test_db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user.id
        ).all()
        assert len(transactions) == 0

        test_db.refresh(user)
        assert user.credit_balance is None or user.credit_balance == 0

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_credits_only(self, mock_award_xp, test_db: Session):
        """Award only credits, no XP"""
        tournament = create_test_tournament(test_db)
        user = create_test_user(test_db, "player@test.com", "Player")

        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            xp_amount=0,
            credits_amount=75,
            reason="Bonus credits"
        )

        assert result is True

        # XP should not be awarded
        mock_award_xp.assert_not_called()

        # Credits should be added
        test_db.refresh(user)
        assert user.credit_balance == 75

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_user_not_found(self, mock_award_xp, test_db: Session):
        """Error: User does not exist"""
        tournament = create_test_tournament(test_db)

        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=99999,
            xp_amount=100,
            credits_amount=50,
            reason="Test"
        )

        assert result is False
        mock_award_xp.assert_not_called()

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_accumulates_credits(self, mock_award_xp, test_db: Session):
        """Credits accumulate on existing balance"""
        tournament = create_test_tournament(test_db)
        user = create_test_user(test_db, "player@test.com", "Player")

        # Set initial credits
        user.credit_balance = 100
        test_db.commit()

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            xp_amount=0,
            credits_amount=50,
            reason="Bonus"
        )

        assert result is True

        test_db.refresh(user)
        assert user.credit_balance == 150  # 100 + 50

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_negative_amounts(self, mock_award_xp, test_db: Session):
        """Test with negative amounts (should still process)"""
        tournament = create_test_tournament(test_db)
        user = create_test_user(test_db, "player@test.com", "Player")
        user.credit_balance = 100
        test_db.commit()

        mock_stats = MagicMock()
        mock_award_xp.return_value = mock_stats

        # This tests that negative amounts pass through
        # (business logic should validate this upstream)
        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            xp_amount=-50,
            credits_amount=-25,
            reason="Penalty"
        )

        assert result is True

        test_db.refresh(user)
        assert user.credit_balance == 75  # 100 - 25

    @patch('app.services.tournament.tournament_xp_service.award_xp')
    def test_award_manual_reward_both_zero(self, mock_award_xp, test_db: Session):
        """Edge case: Both XP and credits are zero"""
        tournament = create_test_tournament(test_db)
        user = create_test_user(test_db, "player@test.com", "Player")

        result = tournament_xp_service.award_manual_reward(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            xp_amount=0,
            credits_amount=0,
            reason="No reward"
        )

        assert result is True
        mock_award_xp.assert_not_called()

        # No transaction created
        transactions = test_db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user.id
        ).all()
        assert len(transactions) == 0
