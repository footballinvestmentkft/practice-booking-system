import logging

from ..database import SessionLocal
from ..models.user import User, UserRole
from ..core.security import get_password_hash
from ..config import settings


logger = logging.getLogger(__name__)


def create_initial_admin():
    """Create initial admin user if not exists"""
    db = SessionLocal()
    try:
        # Check if admin already exists
        admin = db.query(User).filter(User.email == settings.ADMIN_EMAIL).first()
        if admin:
            logger.info("Initial admin user already exists")
            return
        
        # Create admin user
        admin_user = User(
            name=settings.ADMIN_NAME,
            email=settings.ADMIN_EMAIL,
            password_hash=get_password_hash(settings.ADMIN_PASSWORD),
            role=UserRole.ADMIN,
            is_active=True
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        logger.info("Initial admin user created")
    except Exception:
        db.rollback()
        logger.error("Initial admin user creation failed")
    finally:
        db.close()


if __name__ == "__main__":
    create_initial_admin()
