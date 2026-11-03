"""Email notification routes (T025): a test message to check the SMTP configuration."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.problems import ProblemError, problem_responses
from app.api.routes import ServicesDep
from app.domain.jobs import ErrorCode
from app.domain.notifications import EmailTestRequest, EmailTestResult
from app.notifications.render import APP_NAME, RenderedEmail
from app.notifications.transport import build_message

router = APIRouter(prefix="/api/notifications")

TEST_SUBJECT = f"[{APP_NAME}] Test email"
TEST_TEXT = (
    f"This is a test message from {APP_NAME}.\n"
    "Scheduled reports of projects that list this address will be sent the same way.\n"
)


@router.post(
    "/test-email", response_model=EmailTestResult, responses=problem_responses(409, 422, 502)
)
async def send_test_email(body: EmailTestRequest, services: ServicesDep) -> EmailTestResult:
    notifier = services.notifier
    if notifier.transport is None:
        raise ProblemError(
            409,
            ErrorCode.EMAIL_NOT_CONFIGURED,
            "Email is not configured",
            "Set SMTP_HOST and SMTP_FROM (or EMAIL_PROVIDER=fake) and restart the app.",
        )

    email = RenderedEmail(subject=TEST_SUBJECT, text=TEST_TEXT, html=f"<p>{TEST_TEXT}</p>")
    try:
        await notifier.transport.send(build_message(notifier.sender, body.recipients, email))
    except Exception as exc:
        raise ProblemError(
            502, ErrorCode.EMAIL_FAILED, "Sending the email failed", f"{type(exc).__name__}: {exc}"
        ) from None
    return EmailTestResult(recipients=body.recipients, sent_at=datetime.now(UTC))
