"""Проекты пользователя. Доступ всегда ограничен владельцем."""

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from ..board_service import create_default_columns
from ..dependencies import CurrentPrincipal, Db
from ..models import Project
from ..schemas import ProjectCreate, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    principal: CurrentPrincipal,
    db: Db,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    projects = (
        await db.scalars(
            select(Project)
            .where(Project.owner_id == principal.user_id)
            .order_by(Project.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(projects)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, principal: CurrentPrincipal, db: Db):
    project = Project(
        title=payload.title, description=payload.description, owner_id=principal.user_id
    )
    db.add(project)
    await db.flush()
    # Доска без колонок бесполезна: создаём стандартные сразу.
    await create_default_columns(db, project.id)
    result = ProjectResponse.model_validate(project)
    await db.commit()
    return result


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, principal: CurrentPrincipal, db: Db):
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == principal.user_id)
    )
    if project is None:
        # 404 и для чужого проекта: существование чужих ID не раскрывается.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, principal: CurrentPrincipal, db: Db):
    result = await db.execute(
        sql_delete(Project).where(Project.id == project_id, Project.owner_id == principal.user_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
