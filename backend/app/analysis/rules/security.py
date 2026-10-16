"""Security rules (Cloudflare WAF/firewall): surges of blocked and challenged requests.

Mitigated traffic is not an outage, so these are relative-only and capped at high.
"""

from app.analysis.rules.base import Rule
from app.domain.common import Severity, SignalFamily

F = SignalFamily
SECURITY_RULES = (
    Rule(
        "security_blocked_rate",
        F.SECURITY,
        "Blocked requests",
        thresholds="security_event_rate",
        severity_cap=Severity.HIGH,
        title_up="Surge of blocked requests (WAF/firewall)",
        summary="The WAF or firewall blocked more requests than usual; this may be an attack.",
        help=(
            "Review the zone's security events for the source IPs, countries and rules that "
            "matched, and check that legitimate users are not blocked."
        ),
    ),
    Rule(
        "security_challenge_rate",
        F.SECURITY,
        "Challenged requests",
        thresholds="security_event_rate",
        severity_cap=Severity.HIGH,
        title_up="Surge of challenged requests",
        summary=("More requests were challenged (CAPTCHA, JS challenge), often bots or an attack."),
        help=(
            "Review the security events for the rules that matched, and check that real "
            "users or API clients are not being challenged."
        ),
    ),
)
