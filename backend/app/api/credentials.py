"""Shared credential routes (T021): CRUD; secrets are write-only."""

from fastapi import APIRouter, Response

from app.api.problems import ProblemError, problem_responses, validation_problem
from app.api.routes import ServicesDep
from app.container import Services
from app.domain.credentials import Credential, CredentialInput, CredentialList
from app.domain.jobs import ErrorCode
from app.storage.auth import SecretRequired
from app.storage.credentials import CredentialInUse, CredentialNameTaken
from app.storage.secrets import SecretsUnreadable

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


def _not_found(credential_id: str) -> ProblemError:
    return ProblemError(
        404,
        ErrorCode.CREDENTIAL_NOT_FOUND,
        "Credential not found",
        f"No credential {credential_id}.",
    )


def _in_use(exc: CredentialInUse, action: str) -> ProblemError:
    names = ", ".join(u.name for u in exc.used_by)
    return ProblemError(
        409,
        ErrorCode.CREDENTIAL_IN_USE,
        "Credential is in use",
        f"Cannot {action}: used by {names}. Switch those sources to another credential first.",
    )


def _write_error(exc: Exception) -> ProblemError:
    match exc:
        case CredentialNameTaken(name=name):
            return ProblemError(
                409,
                ErrorCode.CREDENTIAL_NAME_TAKEN,
                "Credential name taken",
                f"A credential named {name!r} already exists.",
            )
        case CredentialInUse():
            return _in_use(exc, "change its kind")
        case SecretRequired(field=field):
            return validation_problem(f"body.{field}", "required when no secret is stored")
        case SecretsUnreadable():
            return ProblemError(
                409,
                ErrorCode.CREDENTIALS_UNREADABLE,
                "Stored credential is unreadable",
                "The secret key changed or was lost; re-enter the secret.",
            )
    raise exc


def _forget_health(services: Services, credential: Credential) -> None:
    """Cached health of the projects using the credential is stale after it changes."""
    for usage in credential.used_by:
        services.health_cache.pop(usage.project_id, None)


@router.get("", response_model=CredentialList)
async def list_credentials(services: ServicesDep) -> CredentialList:
    return CredentialList(items=await services.credentials.list())


@router.post("", status_code=201, response_model=Credential, responses=problem_responses(409, 422))
async def create_credential(data: CredentialInput, services: ServicesDep) -> Credential:
    try:
        return await services.credentials.create(data)
    except (CredentialNameTaken, SecretRequired) as exc:
        raise _write_error(exc) from None


@router.get("/{credential_id}", response_model=Credential, responses=problem_responses(404))
async def get_credential(credential_id: str, services: ServicesDep) -> Credential:
    credential = await services.credentials.get(credential_id)
    if credential is None:
        raise _not_found(credential_id)
    return credential


@router.put(
    "/{credential_id}", response_model=Credential, responses=problem_responses(404, 409, 422)
)
async def update_credential(
    credential_id: str, data: CredentialInput, services: ServicesDep
) -> Credential:
    """Rotating the secret applies to every source using the credential at its next use."""
    try:
        credential = await services.credentials.update(credential_id, data)
    except (CredentialNameTaken, CredentialInUse, SecretRequired, SecretsUnreadable) as exc:
        raise _write_error(exc) from None
    if credential is None:
        raise _not_found(credential_id)
    _forget_health(services, credential)
    return credential


@router.delete(
    "/{credential_id}",
    status_code=204,
    response_class=Response,
    responses=problem_responses(404, 409),
)
async def delete_credential(credential_id: str, services: ServicesDep) -> Response:
    """Only an unused credential can be deleted."""
    try:
        deleted = await services.credentials.delete(credential_id)
    except CredentialInUse as exc:
        raise _in_use(exc, "delete it") from None
    if not deleted:
        raise _not_found(credential_id)
    return Response(status_code=204)
