"""`/api/admin/users*`: admins only."""

from fastapi import APIRouter, Depends

from app.modules.auth.deps import require_admin
from app.modules.auth.ports import CurrentUser
from app.modules.users.deps import users_service
from app.modules.users.schemas import StaffCreate, StaffListOut, StaffOut, StaffPatch
from app.modules.users.service import UsersService

router = APIRouter(tags=["users"])


@router.get("/admin/users", dependencies=[Depends(require_admin)])
async def staff(service: UsersService = Depends(users_service)) -> StaffListOut:
    return StaffListOut(items=await service.members())


@router.post("/admin/users", dependencies=[Depends(require_admin)])
async def create_staff(body: StaffCreate, service: UsersService = Depends(users_service)) -> StaffOut:
    return await service.create(body)


@router.patch("/admin/users/{user_id}")
async def update_staff(
    user_id: int,
    body: StaffPatch,
    admin: CurrentUser = Depends(require_admin),
    service: UsersService = Depends(users_service),
) -> StaffOut:
    return await service.update(admin, user_id, body)
