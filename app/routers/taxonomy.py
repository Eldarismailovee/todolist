"""Теги и категории пользователя."""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..dependencies import CurrentPrincipal, Db
from ..models import Category, Tag
from ..schemas import CategoryCreate, CategoryResponse, TagCreate, TagResponse

router = APIRouter(tags=["taxonomy"])


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(principal: CurrentPrincipal, db: Db):
    tags = (
        await db.scalars(select(Tag).where(Tag.owner_id == principal.user_id).order_by(Tag.name))
    ).all()
    return list(tags)


@router.post("/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(payload: TagCreate, principal: CurrentPrincipal, db: Db):
    tag = Tag(owner_id=principal.user_id, name=payload.name, color=payload.color)
    db.add(tag)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой тег уже есть") from exc
    result = TagResponse.model_validate(tag)
    await db.commit()
    return result


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: int, principal: CurrentPrincipal, db: Db):
    result = await db.execute(
        sql_delete(Tag).where(Tag.id == tag_id, Tag.owner_id == principal.user_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Тег не найден")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(principal: CurrentPrincipal, db: Db):
    categories = (
        await db.scalars(
            select(Category).where(Category.owner_id == principal.user_id).order_by(Category.name)
        )
    ).all()
    return list(categories)


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryCreate, principal: CurrentPrincipal, db: Db):
    category = Category(owner_id=principal.user_id, name=payload.name, color=payload.color)
    db.add(category)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Такая категория уже есть") from exc
    result = CategoryResponse.model_validate(category)
    await db.commit()
    return result


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: int, principal: CurrentPrincipal, db: Db):
    """Задачи сохраняются: внешний ключ обнуляется (ON DELETE SET NULL)."""
    result = await db.execute(
        sql_delete(Category).where(
            Category.id == category_id, Category.owner_id == principal.user_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Категория не найдена")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
