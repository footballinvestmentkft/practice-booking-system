"""Admin finance, invoice, and payment management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone
import logging

from sqlalchemy import func as sqlfunc

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User
from ....models.license import UserLicense
from ....models.semester_enrollment import SemesterEnrollment
from ....models.specialization import SpecializationType
from ....models.invoice_request import InvoiceRequest, InvoiceRequestStatus
from ....models.credit_transaction import CreditTransaction, TransactionType

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_financial_kpi(db) -> dict:
    """Build the 8-metric financial KPI dict used by both payments and analytics pages."""
    paid_statuses = [InvoiceRequestStatus.PAID.value, InvoiceRequestStatus.VERIFIED.value]
    total_eur = db.query(sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.amount_eur), 0)).filter(
        InvoiceRequest.status.in_(paid_statuses)).scalar() or 0
    pending_eur = db.query(sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.amount_eur), 0)).filter(
        InvoiceRequest.status == InvoiceRequestStatus.PENDING.value).scalar() or 0
    issued_credits = db.query(sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.credit_amount), 0)).filter(
        InvoiceRequest.status.in_(paid_statuses)).scalar() or 0
    active_balance = db.query(sqlfunc.coalesce(sqlfunc.sum(User.credit_balance), 0)).scalar() or 0
    total_invoices = db.query(InvoiceRequest).count()
    open_invoices = db.query(InvoiceRequest).filter(
        InvoiceRequest.status == InvoiceRequestStatus.PENDING.value).count()
    verified_invoices = db.query(InvoiceRequest).filter(
        InvoiceRequest.status == InvoiceRequestStatus.VERIFIED.value).count()
    users_with_credits = db.query(User).filter(User.credit_balance > 0).count()
    return {
        "total_eur": round(float(total_eur), 2),
        "pending_eur": round(float(pending_eur), 2),
        "issued_credits": int(issued_credits),
        "active_balance": int(active_balance),
        "total_invoices": total_invoices,
        "open_invoices": open_invoices,
        "verified_invoices": verified_invoices,
        "users_with_credits": users_with_credits,
    }


@router.get("/admin/payments", response_class=HTMLResponse)
async def admin_payments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Payment Management page (invoice requests + license payment verification)"""
    _admin_guard(user)

    # 8-metric financial KPI
    fin_kpi = _build_financial_kpi(db)

    # Get all invoice requests (ordered by most recent first)
    invoice_requests = (
        db.query(InvoiceRequest)
        .options(joinedload(InvoiceRequest.user))
        .order_by(InvoiceRequest.created_at.desc())
        .all()
    )

    # Get all UserLicenses that DON'T have any SemesterEnrollment yet (subquery avoids full table scan)
    enrolled_license_ids_subq = db.query(SemesterEnrollment.user_license_id)

    newcomer_licenses = (
        db.query(UserLicense)
        .options(joinedload(UserLicense.user))
        .filter(
            UserLicense.id.notin_(enrolled_license_ids_subq),
            UserLicense.payment_reference_code.isnot(None)
        )
        .order_by(UserLicense.started_at.desc())
        .all()
    )

    newcomer_groups = {}
    for spec_type in SpecializationType:
        newcomer_groups[spec_type.value] = [
            lic for lic in newcomer_licenses
            if lic.specialization_type == spec_type.value
        ]

    return templates.TemplateResponse(
        "admin/payments.html",
        {
            "request": request,
            "user": user,
            "invoice_requests": invoice_requests,
            "newcomer_groups": newcomer_groups,
            "SpecializationType": SpecializationType,
            "fin_kpi": fin_kpi,
        }
    )


@router.post("/admin/invoices/{invoice_id}/verify")
async def admin_invoice_verify(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Verify invoice payment and credit the student account (cookie auth)."""
    _admin_guard(user)
    invoice = db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "verified":
        raise HTTPException(status_code=400, detail="Invoice already verified")
    if invoice.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot verify cancelled invoice")

    student = db.query(User).filter(User.id == invoice.user_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    old_balance = student.credit_balance
    invoice.status = "verified"
    invoice.verified_at = datetime.now(timezone.utc)
    student.credit_balance += invoice.credit_amount
    student.credit_purchased = (student.credit_purchased or 0) + invoice.credit_amount
    ct = CreditTransaction(
        user_id=student.id,
        transaction_type=TransactionType.PURCHASE.value,
        amount=invoice.credit_amount,
        balance_after=student.credit_balance,
        description=f"Invoice #{invoice.id} verified by admin",
        idempotency_key=f"invoice-verify-{invoice.id}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    db.refresh(invoice)
    db.refresh(student)

    return JSONResponse({"success": True, "credits_added": invoice.credit_amount,
                         "student_name": student.name, "new_balance": student.credit_balance})


@router.post("/admin/invoices/{invoice_id}/cancel")
async def admin_invoice_cancel(
    invoice_id: int,
    request: Request,
    reason: str = Form("No reason provided"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Cancel an invoice request (cookie auth)."""
    _admin_guard(user)
    invoice = db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "verified":
        raise HTTPException(status_code=400, detail="Cannot cancel verified invoice")
    if invoice.status == "cancelled":
        raise HTTPException(status_code=400, detail="Invoice already cancelled")

    invoice.status = "cancelled"
    db.commit()
    return JSONResponse({"success": True, "message": f"Invoice cancelled. Reason: {reason}"})


@router.post("/admin/invoices/{invoice_id}/unverify")
async def admin_invoice_unverify(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Unverify invoice (reverts credits) — cookie auth."""
    _admin_guard(user)
    invoice = db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "verified":
        raise HTTPException(status_code=400, detail="Invoice must be verified to unverify")

    student = db.query(User).filter(User.id == invoice.user_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    deducted = min(invoice.credit_amount, student.credit_balance or 0)
    student.credit_balance = max(0, (student.credit_balance or 0) - invoice.credit_amount)
    student.credit_purchased = max(0, (student.credit_purchased or 0) - invoice.credit_amount)
    invoice.status = "pending"
    invoice.verified_at = None
    ct = CreditTransaction(
        user_id=student.id,
        transaction_type=TransactionType.REFUND.value,
        amount=-deducted,
        balance_after=student.credit_balance,
        description=f"Invoice #{invoice.id} unverified by admin",
        idempotency_key=f"invoice-unverify-{invoice.id}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    db.refresh(invoice)
    db.refresh(student)

    return JSONResponse({"success": True, "credits_removed": invoice.credit_amount,
                         "student_name": student.name, "new_balance": student.credit_balance})
