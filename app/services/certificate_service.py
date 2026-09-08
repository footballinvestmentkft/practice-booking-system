"""
Certificate Service
Handles automatic certificate generation and verification
"""

from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from ..models import (
    Track, UserTrackProgress, 
    CertificateTemplate, IssuedCertificate,
    User
)


class CertificateGenerationError(Exception):
    """Custom exception for certificate generation errors"""


class CertificateService:
    """Service for managing certificates and automatic generation"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_certificate_template(self, track_id: str, title: str, 
                                  description: str = None, 
                                  design_template: str = None,
                                  validation_rules: Dict = None) -> CertificateTemplate:
        """Create a certificate template for a track"""
        template = CertificateTemplate(
            track_id=track_id,
            title=title,
            description=description,
            design_template=design_template or self._get_default_template(),
            validation_rules=validation_rules or self._get_default_validation_rules()
        )
        self.db.add(template)
        self.db.commit()
        self.db.refresh(template)
        return template
    
    def _get_default_template(self) -> str:
        """Get default certificate HTML template"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                .certificate {
                    width: 800px;
                    height: 600px;
                    border: 3px solid #1e3a8a;
                    margin: 20px auto;
                    padding: 40px;
                    text-align: center;
                    font-family: Georgia, serif;
                    background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
                }
                .header { font-size: 28px; color: #1e3a8a; margin-bottom: 20px; }
                .title { font-size: 36px; color: #dc2626; margin: 30px 0; }
                .recipient { font-size: 24px; margin: 20px 0; }
                .completion { font-size: 18px; color: #374151; margin: 20px 0; }
                .signature { margin-top: 40px; }
                .certificate-id { font-size: 12px; color: #6b7280; margin-top: 30px; }
            </style>
        </head>
        <body>
            <div class="certificate">
                <div class="header">LFA FOOTBALL ACADEMY</div>
                <div class="title">CERTIFICATE OF COMPLETION</div>
                <div class="recipient">
                    This is to certify that<br>
                    <strong>{{user_name}}</strong><br>
                    has successfully completed the
                </div>
                <div class="completion">
                    <strong>{{track_name}}</strong><br>
                    Track Program<br><br>
                    Completed on {{completion_date}}<br>
                    Final Grade: {{final_grade}}%
                </div>
                <div class="signature">
                    <hr style="width: 200px; margin: 0 auto;">
                    <p>Director, LFA Academy</p>
                </div>
                <div class="certificate-id">
                    Certificate ID: {{certificate_id}}<br>
                    Verify at: {{verification_url}}
                </div>
            </div>
        </body>
        </html>
        """
    
    def _get_default_validation_rules(self) -> Dict:
        """Get default validation rules for certificate generation"""
        return {
            "minimum_completion_percentage": 100.0,
            "minimum_grade": 60.0,
            "required_modules": "all_mandatory",
            "allow_retakes": True
        }
    
    def generate_certificate(self, track_progress: "UserTrackProgress") -> IssuedCertificate:
        """Generate certificate for completed track"""
        if not track_progress.is_ready_for_certificate:
            raise CertificateGenerationError("Track progress not ready for certificate generation")
        
        # Get certificate template
        template = self.db.query(CertificateTemplate)\
            .filter(CertificateTemplate.track_id == track_progress.track_id)\
            .first()
        
        if not template:
            raise CertificateGenerationError(f"No certificate template found for track {track_progress.track_id}")
        
        # Generate unique identifier
        track = track_progress.track
        unique_id = IssuedCertificate.generate_unique_identifier(track.code)
        
        # Calculate final grade (average of all module grades)
        final_grade = self._calculate_final_grade(track_progress)
        
        # Generate verification hash
        verification_hash = IssuedCertificate.generate_verification_hash(
            unique_id,
            str(track_progress.user_id),
            datetime.utcnow()
        )
        
        # Create certificate metadata
        cert_metadata = {
            "track_name": track.name,
            "track_code": track.code,
            "completion_percentage": track_progress.completion_percentage,
            "final_grade": final_grade,
            "enrollment_date": track_progress.enrollment_date.isoformat(),
            "completion_date": track_progress.completed_at.isoformat(),
            "duration_days": track_progress.duration_days,
            "modules_completed": len([mp for mp in track_progress.module_progresses 
                                   if mp.status.value == "completed"])
        }
        
        # Create issued certificate
        certificate = IssuedCertificate(
            certificate_template_id=template.id,
            user_id=track_progress.user_id,
            unique_identifier=unique_id,
            issue_date=datetime.utcnow(),
            completion_date=track_progress.completed_at,
            verification_hash=verification_hash,
            cert_metadata=cert_metadata
        )
        
        self.db.add(certificate)
        self.db.commit()
        self.db.refresh(certificate)
        
        return certificate
    
    def _calculate_final_grade(self, track_progress: "UserTrackProgress") -> float:
        """Calculate final grade from module grades"""
        module_progresses = [mp for mp in track_progress.module_progresses 
                           if mp.status.value == "completed" and mp.grade is not None]
        
        if not module_progresses:
            return 0.0
        
        total_grade = sum([mp.grade for mp in module_progresses])
        return round(total_grade / len(module_progresses), 2)
    
    def verify_certificate(self, unique_identifier: str) -> Dict[str, Any]:
        """Verify certificate authenticity and return details"""
        certificate = self.db.query(IssuedCertificate)\
            .filter(IssuedCertificate.unique_identifier == unique_identifier)\
            .first()
        
        if not certificate:
            return {
                "valid": False,
                "error": "Certificate not found"
            }
        
        # Check if revoked
        if certificate.is_revoked:
            return {
                "valid": False,
                "error": "Certificate has been revoked",
                "revoked_at": certificate.revoked_at,
                "revoked_reason": certificate.revoked_reason
            }
        
        # Verify authenticity
        if not certificate.verify_authenticity():
            return {
                "valid": False,
                "error": "Certificate authenticity could not be verified"
            }
        
        # Return certificate details
        user = self.db.query(User).filter(User.id == certificate.user_id).first()
        template = certificate.template
        track = template.track
        
        return {
            "valid": True,
            "certificate_id": certificate.unique_identifier,
            "user_name": user.name,
            "track_name": track.name,
            "track_code": track.code,
            "issue_date": certificate.issue_date,
            "completion_date": certificate.completion_date,
            "final_grade": certificate.cert_metadata.get("final_grade", "N/A"),
            "duration_days": certificate.cert_metadata.get("duration_days", 0),
            "verification_hash": certificate.verification_hash
        }
    
    def get_user_certificates(self, user_id: str) -> List[IssuedCertificate]:
        """Get all certificates for a user"""
        return self.db.query(IssuedCertificate)\
            .filter(IssuedCertificate.user_id == user_id)\
            .filter(IssuedCertificate.is_revoked == False)\
            .order_by(IssuedCertificate.issue_date.desc())\
            .all()
    
    def revoke_certificate(self, certificate_id: str, reason: str) -> IssuedCertificate:
        """Revoke a certificate"""
        certificate = self.db.query(IssuedCertificate)\
            .filter(IssuedCertificate.id == certificate_id)\
            .first()
        
        if not certificate:
            raise ValueError("Certificate not found")
        
        certificate.revoke(reason)
        self.db.commit()
        
        return certificate
    
    def generate_certificate_pdf(self, certificate_id: str) -> bytes:
        """Render a self-contained, verifiable one-page certificate PDF."""
        certificate = self.db.query(IssuedCertificate)\
            .filter(IssuedCertificate.id == certificate_id)\
            .first()
        
        if not certificate:
            raise ValueError("Certificate not found")
        
        metadata = certificate.cert_metadata or {}
        template = certificate.template
        track = getattr(template, "track", None)
        user = certificate.user

        title = getattr(template, "title", None) or "LFA Certificate"
        track_name = (
            metadata.get("track_name")
            or getattr(track, "name", None)
            or "LFA Education Program"
        )
        learner_name = getattr(user, "name", None) or f"Learner {certificate.user_id}"
        issue_date = certificate.issue_date.strftime("%Y-%m-%d")
        completion_date = (
            certificate.completion_date.strftime("%Y-%m-%d")
            if certificate.completion_date
            else "N/A"
        )
        grade = metadata.get("final_grade", "N/A")

        lines = [
            ("F2", 26, 421, 500, title),
            ("F1", 14, 421, 440, "This certifies that"),
            ("F2", 22, 421, 395, learner_name),
            ("F1", 14, 421, 350, "successfully completed"),
            ("F2", 18, 421, 312, track_name),
            ("F1", 11, 421, 250, f"Completion date: {completion_date}"),
            ("F1", 11, 421, 228, f"Final grade: {grade}"),
            ("F1", 10, 421, 170, f"Issued: {issue_date}"),
            ("F2", 10, 421, 145, f"Certificate ID: {certificate.unique_identifier}"),
            ("F1", 9, 421, 115, "Verify this certificate using its Certificate ID."),
        ]
        commands = [
            "0.08 0.20 0.36 rg",
            "2 w 36 36 770 523 re S",
        ]
        for font, size, x, y, value in lines:
            escaped = self._pdf_text(value)
            commands.append(
                f"BT /{font} {size} Tf {x} {y} Td ({escaped}) Tj ET"
            )
        stream = "\n".join(commands).encode("latin-1")

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
                b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> "
                b"/Contents 4 0 R >>"
            ),
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream
            + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        ]
        return self._build_pdf(objects)

    @staticmethod
    def _pdf_text(value: Any) -> str:
        text_value = str(value).encode("latin-1", "replace").decode("latin-1")
        return (
            text_value.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    @staticmethod
    def _build_pdf(objects: List[bytes]) -> bytes:
        result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, body in enumerate(objects, start=1):
            offsets.append(len(result))
            result.extend(f"{index} 0 obj\n".encode("ascii"))
            result.extend(body)
            result.extend(b"\nendobj\n")

        xref_offset = len(result)
        result.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        result.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        result.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_offset}\n%%EOF\n"
            ).encode("ascii")
        )
        return bytes(result)
    
    def get_certificate_analytics(self) -> Dict[str, Any]:
        """Get certificate generation analytics"""
        # Total certificates issued
        total_issued = self.db.query(IssuedCertificate).count()
        
        # Certificates by track
        from sqlalchemy import func
        certificates_by_track = self.db.query(
            Track.name,
            Track.code,
            func.count(IssuedCertificate.id).label('count')
        )\
        .join(CertificateTemplate, CertificateTemplate.track_id == Track.id)\
        .join(IssuedCertificate, IssuedCertificate.certificate_template_id == CertificateTemplate.id)\
        .group_by(Track.id, Track.name, Track.code)\
        .all()
        
        # Revoked certificates
        revoked_count = self.db.query(IssuedCertificate)\
            .filter(IssuedCertificate.is_revoked == True)\
            .count()
        
        return {
            "total_certificates_issued": total_issued,
            "certificates_by_track": [
                {"track_name": track[0], "track_code": track[1], "count": track[2]} 
                for track in certificates_by_track
            ],
            "revoked_certificates": revoked_count,
            "active_certificates": total_issued - revoked_count
        }
