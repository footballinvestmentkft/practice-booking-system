"""
User credits and billing endpoints
Credit balance, transactions, and invoice requests
"""
from typing import Any
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from .....database import get_db
from .....dependencies import get_current_user, get_current_user_web
from .....models.user import User

router = APIRouter()


@router.post("/request-invoice")
async def request_invoice(
    request_data: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # Changed to support both web cookies and API Bearer tokens
) -> Any:
    """
    Request invoice for credit purchase - creates InvoiceRequest with unique payment reference (Supports both web cookies and API tokens)

    Request body:
    {
        "package_type": "PACKAGE_500",  # One of: PACKAGE_250, PACKAGE_500, PACKAGE_1000, PACKAGE_2000
        "specialization_type": "LFA_FOOTBALL_PLAYER"  # Optional
    }

    Returns:
    {
        "id": int,
        "payment_reference": str,  # e.g., "LFA-20260222-174530-00123-A3F2"
        "amount_eur": float,
        "credit_amount": int,
        "status": "pending"
    }
    """
    from .....models.invoice_request import InvoiceRequest, InvoiceRequestStatus
    from datetime import datetime, timezone
    import hashlib

    # Define package mappings
    PACKAGE_MAPPINGS = {
        "PACKAGE_250": {"amount_eur": 250.0, "credit_amount": 250},
        "PACKAGE_500": {"amount_eur": 500.0, "credit_amount": 500},
        "PACKAGE_1000": {"amount_eur": 1000.0, "credit_amount": 1000},
        "PACKAGE_2000": {"amount_eur": 2000.0, "credit_amount": 2000},
    }

    # Validate request data
    package_type = request_data.get("package_type")
    if not package_type or package_type not in PACKAGE_MAPPINGS:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid package_type. Must be one of: {', '.join(PACKAGE_MAPPINGS.keys())}"
        )

    # Check for existing active invoice (prevent duplicates)
    existing_invoice = db.query(InvoiceRequest).filter(
        InvoiceRequest.user_id == current_user.id,
        InvoiceRequest.status == InvoiceRequestStatus.PENDING.value
    ).first()

    if existing_invoice:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a pending invoice request (ID: {existing_invoice.id}, ref: {existing_invoice.payment_reference})"
        )

    # Get package details
    package_info = PACKAGE_MAPPINGS[package_type]
    amount_eur = package_info["amount_eur"]
    credit_amount = package_info["credit_amount"]

    # Get specialization (optional)
    specialization = request_data.get("specialization_type")

    # Create invoice request (payment_reference will be generated after commit to get ID)
    invoice = InvoiceRequest(
        user_id=current_user.id,
        amount_eur=amount_eur,
        credit_amount=credit_amount,
        specialization=specialization,
        status=InvoiceRequestStatus.PENDING.value,
        payment_reference="TEMP"  # Temporary, will be updated after commit
    )

    db.add(invoice)
    db.flush()  # Flush to get the ID without committing

    # Generate unique payment reference: LFA-YYYYMMDD-HHMMSS-ID-HASH
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S")
    id_part = f"{invoice.id:05d}"  # 5-digit zero-padded ID

    # Generate 4-char hash from timestamp + id + user_id
    hash_input = f"{now.timestamp()}{invoice.id}{current_user.id}".encode()
    hash_part = hashlib.md5(hash_input).hexdigest()[:4].upper()

    payment_reference = f"LFA-{date_part}-{time_part}-{id_part}-{hash_part}"

    # Update payment reference
    invoice.payment_reference = payment_reference

    db.commit()
    db.refresh(invoice)

    return {
        "id": invoice.id,
        "payment_reference": invoice.payment_reference,
        "amount_eur": invoice.amount_eur,
        "credit_amount": invoice.credit_amount,
        "status": invoice.status,
        "message": f"Invoice request created. Please transfer {amount_eur} EUR to our bank account with reference: {payment_reference}"
    }

@router.get("/credit-balance")
async def get_credit_balance(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # Changed to support both web cookies and API Bearer tokens
) -> Any:
    """
    Get current user's credit balance and invoice counts (for polling/auto-refresh, supports both web cookies and API tokens)
    """
    from .....models.invoice_request import InvoiceRequest

    # Get invoice counts
    invoice_counts = db.query(
        InvoiceRequest.status,
        func.count(InvoiceRequest.id).label('count')
    ).filter(
        InvoiceRequest.user_id == current_user.id
    ).group_by(InvoiceRequest.status).all()

    invoice_status_counts = {
        'pending': 0,
        'verified': 0,
        'paid': 0,
        'cancelled': 0,
        'total': 0
    }

    for status, count in invoice_counts:
        invoice_status_counts[status] = count
        invoice_status_counts['total'] += count

    return {
        "credit_balance": current_user.credit_balance,
        "credit_purchased": current_user.credit_purchased,
        "credit_used": current_user.credit_purchased - current_user.credit_balance,
        "invoice_counts": invoice_status_counts
    }


@router.get("/me/credit-transactions")
def get_my_credit_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of transactions to return"),
    offset: int = Query(default=0, ge=0, description="Number of transactions to skip")
) -> Any:
    """
    Get current user's credit transaction history.

    Available for ALL users (Student, Instructor, Admin) to see their own transactions.
    Shows:
    - License renewals (credit deductions)
    - Credit purchases
    - Semester enrollments
    - Refunds
    - Admin adjustments
    """
    from .....models.credit_transaction import CreditTransaction
    from .....models.license import UserLicense

    # Get all user's licenses
    user_licenses = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id
    ).all()

    # Always include direct user-level transactions (user_license_id IS NULL) —
    # invoices, coupons, and admin grants are stored this way and must be visible
    # even for users who have no UserLicense records.
    from sqlalchemy import or_
    user_direct_filter = (
        (CreditTransaction.user_id == current_user.id) &
        (CreditTransaction.user_license_id == None)
    )

    if user_licenses:
        license_ids = [lic.id for lic in user_licenses]
        tx_filter = or_(
            CreditTransaction.user_license_id.in_(license_ids),
            user_direct_filter,
        )
    else:
        tx_filter = user_direct_filter

    transactions_query = db.query(CreditTransaction).filter(
        tx_filter
    ).order_by(CreditTransaction.created_at.desc())

    total_count = transactions_query.count()

    transactions = transactions_query.limit(limit).offset(offset).all()

    return {
        "transactions": [tx.to_dict() for tx in transactions],
        "total_count": total_count,
        "credit_balance": current_user.credit_balance,
        "showing": len(transactions),
        "limit": limit,
        "offset": offset
    }
