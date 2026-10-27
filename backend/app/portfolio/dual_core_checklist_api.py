"""Authenticated manual cards. No broker, scheduler or portfolio write dependency."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..shared.security import current_user_id
from . import dual_core_checklist as service
from .dual_core_checklist import CardInput, Phase
from .dual_core_checklist_models import ExecutionCard

class BoundedCardRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method == 'PUT':
                size = 0
                chunks = []
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 131072:
                        raise HTTPException(413, 'card exceeds 128 KiB')
                    chunks.append(chunk)
                request._body = b''.join(chunks)
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [{k: v for k, v in error.items() if k not in {"input", "ctx"}}
                          for error in exc.errors()]
                raise RequestValidationError(errors) from None
        return bounded


router = APIRouter(prefix='/api/portfolio/dual-core/cards', tags=['dual-core-cards'], route_class=BoundedCardRoute)


@router.get('')
def list_cards(trade_date: date, db: Session = Depends(get_db), user_id: int = Depends(current_user_id)):
    cards = db.scalars(select(ExecutionCard).where(ExecutionCard.user_id == user_id,
        ExecutionCard.trade_date == str(trade_date), ExecutionCard.deleted.is_(False)).order_by(ExecutionCard.phase)).all()
    return {'trade_date': str(trade_date), 'cards': [service.serialize(db, c) for c in cards],
        'day_guard': service.day_guard(db, user_id, trade_date), 'calendar_status': 'unverified'}


@router.get('/{trade_date}/{phase}')
def get_card(trade_date: date, phase: Phase, db: Session = Depends(get_db), user_id: int = Depends(current_user_id)):
    card = service.find_card(db, user_id, trade_date, phase)
    if card is None or card.deleted:
        raise HTTPException(404, 'card not found')
    return service.serialize(db, card)


@router.put('/{trade_date}/{phase}')
def put_card(trade_date: date, phase: Phase, body: CardInput, db: Session = Depends(get_db), user_id: int = Depends(current_user_id)):
    return service.save_card(db, user_id, trade_date, phase, body)


@router.get('/{trade_date}/{phase}/audit')
def get_audit(trade_date: date, phase: Phase, db: Session = Depends(get_db), user_id: int = Depends(current_user_id)):
    card = service.find_card(db, user_id, trade_date, phase)
    if card is None:
        raise HTTPException(404, 'card not found')
    return {'items': service.audit(db, card)}


@router.get('/{trade_date}/{phase}/export')
def export_card(trade_date: date, phase: Phase, db: Session = Depends(get_db), user_id: int = Depends(current_user_id)):
    card = get_card(trade_date, phase, db, user_id)
    history = get_audit(trade_date, phase, db, user_id)['items']
    return Response(json.dumps({'card': card, 'audit': history}, ensure_ascii=False),
        media_type='application/json', headers={'Content-Disposition':
            f'attachment; filename="dual-core-{trade_date}-{phase}.json"'})


@router.delete('/{trade_date}/{phase}')
def delete_card(trade_date: date, phase: Phase, revision: int = Query(ge=1), db: Session = Depends(get_db), user_id: int = Depends(current_user_id)):
    card = service.find_card(db, user_id, trade_date, phase)
    if card is None or card.deleted:
        raise HTTPException(404, 'card not found')
    return service.delete_card(db, card, revision)
